"""Order search: no clock-grid branches; intervals certify an executable order.

Clocks in witnesses are internal examples, not user commitments. Interval ranges
are conditional on order/durations and estimated travel; live navigation is separate.
"""
from dataclasses import dataclass
import copy
import math

MEALS = ("breakfast", "lunch", "dinner")
WINDOWS = {"breakfast": (300, 660, 30), "lunch": (600, 960, 45), "dinner": (960, 1380, 45)}
PREFERRED = {"breakfast": 480, "lunch": 720, "dinner": 1080}


def minute(s):
    h, m = map(int, str(s).split(":"))
    if h < 0 or not 0 <= m < 60 or h > 24 or (h == 24 and m):
        raise ValueError("Invalid same-day clock: " + str(s))
    return h * 60 + m


def clock(m):
    if not 0 <= m <= 1440:
        raise ValueError("Clock outside day")
    return f"{int(m)//60:02d}:{int(m)%60:02d}"


@dataclass(frozen=True)
class Policy:
    meal_gap: int = 120  # free time between the end and start of successive meals
    sleep_minutes: int = 480
    return_buffer_train: int = 60
    return_buffer_airplane: int = 120
    default_visit: int = 90
    beam_width: int = 48
    orders_to_check: int = 24
    minimum_total_visit: int = 120  # delivery preference, not a feasibility axiom
    max_city_transit: int = 240
    max_walk: int = 120
    route_cost_weight: float = .08


@dataclass
class Item:
    activity: dict
    lower: int
    upper: int  # latest START
    duration: int
    fixed: bool = False

    @property
    def name(self):
        return self.activity.get("position", self.activity.get("start"))

    @property
    def kind(self):
        return self.activity["type"]


def bounds(items, travel, start, finish, meal_gap=120):
    """Forward/backward interval propagation on chain + nonadjacent meal edges."""
    n = len(items)
    edges = [(i-1, i, items[i-1].duration + travel[i]) for i in range(1, n)]
    meals = [i for i, a in enumerate(items) if a.kind in MEALS]
    edges += [(a, b, items[a].duration + meal_gap) for a, b in zip(meals, meals[1:])]
    lo = [a.lower for a in items]
    hi = [a.upper for a in items]
    if not n:
        return [], []
    lo[0] = max(lo[0], start + travel[0])
    hi[-1] = min(hi[-1], finish-items[-1].duration)
    for i in range(n):
        for a, b, delta in edges:
            if b == i:
                lo[b] = max(lo[b], lo[a]+delta)
        if lo[i] > hi[i]:
            return None
    for i in reversed(range(n)):
        for a, b, delta in edges:
            if a == i:
                hi[a] = min(hi[a], hi[b]-delta)
        if lo[i] > hi[i]:
            return None
    return lo, hi


def orders(items, start_position, estimate, policy):
    """Bounded ordering beam; preserves meals and puts hotel/return at the end."""
    if len(items) > 12:
        return []  # explicit unsupported capacity, never silently drop an item
    beam = [(0.0, (), tuple(range(len(items))), start_position)]
    for _ in items:
        extended = []
        for score, order, pending, position in beam:
            for i in pending:
                item = items[i]
                if item.kind in ("accommodation", "train", "airplane") and len(pending) > 1:
                    continue
                if item.kind in MEALS and any(items[j].kind in MEALS and
                     MEALS.index(items[j].kind) < MEALS.index(item.kind) for j in pending):
                    continue
                distance = estimate(position, item.name)
                if not math.isfinite(distance):
                    continue
                # Earlier closing/locked items get modest priority; no clock is committed.
                urgency = max(0, 900-item.upper)/60
                extended.append((score+distance+urgency*len(order), order+(i,),
                                 tuple(j for j in pending if j != i), item.name))
        beam = sorted(extended, key=lambda x: (x[0], x[1]))[:policy.beam_width]
        if not beam:
            break
    return [tuple(items[i] for i in row[1]) for row in beam[:policy.orders_to_check] if not row[2]]


def realize(order, start_position, start, finish, route, policy):
    """Re-query exact departure-time routes, then retain a feasible private witness."""
    positions = [start_position] + [a.name for a in order]
    lengths = []
    for i, a in enumerate(order):
        legs = route(positions[i], a.name, max(start, min(a.lower, 1200)))
        if legs is None:
            return None
        buffer = policy.return_buffer_train if a.kind == "train" else policy.return_buffer_airplane if a.kind == "airplane" else 0
        lengths.append(sum(minute(x["end_time"])-minute(x["start_time"]) for x in legs)+buffer)
    for _ in range(4):
        propagated = bounds(order, lengths, start, finish, policy.meal_gap)
        if propagated is None:
            return None
        lo, hi = propagated
        previous = start
        previous_meal_end = None
        activities, actual_lengths, windows = [], [], []
        for i, item in enumerate(order):
            earliest = max(lo[i], previous+lengths[i])
            if item.kind in MEALS and previous_meal_end is not None:
                earliest = max(earliest, previous_meal_end+policy.meal_gap)
            preferred = PREFERRED.get(item.kind, earliest)
            chosen = min(hi[i], max(earliest, preferred))
            # Wait at the prior place instead of committing to an early departure.
            depart = max(previous, chosen-lengths[i])
            legs = route(positions[i], item.name, depart)
            if legs is None:
                return None
            actual = sum(minute(x["end_time"])-minute(x["start_time"]) for x in legs)
            buffer = policy.return_buffer_train if item.kind == "train" else policy.return_buffer_airplane if item.kind == "airplane" else 0
            actual_lengths.append(actual+buffer)
            arrived = minute(legs[-1]["end_time"]) if legs else depart
            chosen = max(chosen, arrived)
            if chosen > hi[i]:
                break
            a = copy.deepcopy(item.activity)
            a.update(start_time=clock(chosen), end_time=clock(chosen+item.duration), transports=legs)
            if item.kind == "accommodation":
                a["end_time"] = "24:00"
            # Ticket arrival may be on the next day; preserve its catalog clock.
            if item.kind in ("train", "airplane"):
                a["end_time"] = item.activity["end_time"]
                buffer = policy.return_buffer_train if item.kind == "train" else policy.return_buffer_airplane
                if arrived > chosen-buffer:
                    return None
            activities.append(a)
            windows.append({"name": item.name, "earliest": clock(lo[i]), "latest": clock(hi[i]),
                            "fixed": item.fixed, "duration_minutes": item.duration})
            previous = chosen+item.duration
            if item.kind in MEALS:
                previous_meal_end = previous
        if len(activities) == len(order) and actual_lengths == lengths:
            return {"activities": activities, "windows": windows}
        lengths = [max(old, actual_lengths[i]) if i < len(actual_lengths) else old
                   for i, old in enumerate(lengths)]
    return None


def metrics(plan):
    days = []
    for day in plan.get("itinerary", []):
        acts = day["activities"]
        legs = [t for a in acts for t in a.get("transports", [])]
        durations = lambda xs: sum(minute(x["end_time"])-minute(x["start_time"]) for x in xs)
        meals = [a for a in acts if a["type"] in MEALS]
        days.append({"day": day["day"], "visit_minutes": durations([a for a in acts if a["type"]=="attraction"]),
                     "city_transit_minutes": durations(legs),
                     "walk_minutes": durations([t for t in legs if t["mode"]=="walk"]),
                     "meal_gaps": [minute(b["start_time"])-minute(a["end_time"]) for a,b in zip(meals,meals[1:])]})
    return {"days": days, "cost": round(sum(a.get("cost",0)+sum(t.get("cost",0) for t in a.get("transports",[]))
                    for d in plan.get("itinerary",[]) for a in d["activities"]),2),
            **{k:sum(d[k] for d in days) for k in ("visit_minutes","city_transit_minutes","walk_minutes")}}


def review(plan, policy=Policy(), allow_night=False):
    """Delivery concerns independent of ordering. These are prototype defaults."""
    m = metrics(plan)
    issues = []
    if m["visit_minutes"] < policy.minimum_total_visit:
        issues.append("too_little_total_visit")
    for day in plan.get("itinerary", []):
        dm = next(x for x in m["days"] if x["day"]==day["day"])
        if any(g < policy.meal_gap for g in dm["meal_gaps"]):
            issues.append("meal_spacing")
        if dm["city_transit_minutes"] > policy.max_city_transit:
            issues.append("heavy_daily_transit")
        if dm["walk_minutes"] > policy.max_walk:
            issues.append("heavy_daily_walk")
        for a in day["activities"]:
            if a["type"] in ("train","airplane") and not allow_night and any(
                minute(a[k]) < 360 or minute(a[k]) >= 1380 for k in ("start_time","end_time")):
                issues.append("night_intercity_needs_confirmation")
    return {"issues": sorted(set(issues)), "metrics": m, "delivery_ready": not issues}


def public_view(plan, appointments=()):
    """No ordinary start/end clock; fixed appointments and transport stay explicit."""
    days=[]
    for d in plan.get("itinerary",[]):
        items=[]
        for i,a in enumerate(d["activities"]):
            locked = next((p for p in appointments if p["day"]==d["day"] and p["name"]==a.get("position")),None)
            item={"order":i+1,"type":a["type"],"place":a.get("position",a.get("start","")+" → "+a.get("end","")),
                  "reference_cost":a.get("cost"),"price_status":"reference",
                  "transfer_minutes":sum(minute(t["end_time"])-minute(t["start_time"]) for t in a.get("transports",[])),
                  "transfer_modes":list(dict.fromkeys(t["mode"] for t in a.get("transports",[])))}
            if a["type"] in ("train","airplane") or locked:
                item["fixed_start"]=a["start_time"]
                item["fixed_end"]=a["end_time"]
            elif a["type"]!="accommodation":
                item["suggested_duration_minutes"]=minute(a["end_time"])-minute(a["start_time"])
            items.append(item)
        days.append({"day":d["day"],"sequence":items})
    return {"target_city":plan.get("target_city"),"days":days,
            "notice":"普通活动只规定顺序；时长和交通基于本地参考数据。预约、返程和营业条件仍需遵守。"}

