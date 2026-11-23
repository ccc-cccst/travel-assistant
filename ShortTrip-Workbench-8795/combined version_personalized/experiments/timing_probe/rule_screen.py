"""Offline paired tests of C-group budget and pre-route time-window pruning."""

import argparse
import ast
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import product
import json
import math
import os
from pathlib import Path
import random
import re
from statistics import median
import sys
from textwrap import dedent
from time import perf_counter
from types import SimpleNamespace

from live_report import read, verify
from live_suite import PROJECT, digest
from speed_report import table
from speed_study import ARTIFACTS, DATASET, PILOT


TIERS = ("easy", "medium", "human")
ANCHORS = {"easy": ("easy_025", "easy_225"), "medium": ("medium_013", "medium_113"),
           "human": ("human_038", "human_073", "human_088")}
STRATA = {"easy": ((1, True), (1, True), (2, False), (2, False), (3, False), (3, False)),
          "medium": ((2, True), (2, True), (3, True), (3, True), (4, True), (4, True)),
          "human": ((1, False), (2, True), (3, False), (4, True), (5, True))}
BUDGET = re.compile(
    r"total_cost\s*=\s*0\s+for activity in allactivities\(plan\):\s*"
    r"total_cost\s*\+=\s*activity_cost\(activity\)\s*"
    r"(?:total_cost\s*\+=\s*)?\+?\s*innercity_transport_cost\(activity_transports\(activity\)\)\s*"
    r"result\s*=\s*\(total_cost\s*<=\s*(people_count\(plan\)\s*\*\s*)?(\d+(?:\.\d+)?)\)\s*"
)


def program_constraints(query):
    codes = query["hard_logic_py"]
    people = query["people_number"]
    budget = min((float(match[2]) * (people if match[1] else 1)
                  for code in codes if (match := BUDGET.fullmatch(code.strip()))), default=None)
    supported = [] if budget is None else [f"cost<={budget:g}"]
    for code in codes:
        clock = re.search(r"^result\s*=\s*\(return_start_time\s*(<=|<)\s*['\"](\d\d:\d\d)['\"]\)\s*$", code, re.M)
        modes = re.search(r"^result\s*=\s*\((\{[^{}]+\})\s*==\s*intercity_transport_set\)\s*$", code, re.M)
        if clock:
            supported.append(f"return_start_time{clock[1]}{clock[2]!r}")
        if modes and ast.literal_eval(modes[1]) <= {"train", "airplane"}:
            supported.append(f"intercity_transport=={modes[1]}")
        for helper, field in (("room_count", "rooms"), ("room_type", "room_type")):
            match = re.search(rf"{helper}\(activity\)\s*!=\s*(\d+)\s*:\s*result\s*=\s*False", code)
            if match:
                supported.append(f"{field}=={match[1]}")
    return supported, budget


def select_queries():
    rng = random.Random(20261116)
    cases = {path.stem: read(path) for path in sorted(DATASET.glob("*.json"))}
    marks = {case: program_constraints(query) for case, query in cases.items()}
    selected = []
    for tier in TIERS:
        selection = list(ANCHORS[tier])
        for days, with_budget in STRATA[tier]:
            options = [case for case, query in cases.items() if case.startswith(tier) and case not in selection
                       and query["days"] == days and (marks[case][1] is not None) == with_budget]
            if not options:
                raise ValueError(f"Empty stratum {tier} {days} {with_budget}")
            selection.append(rng.choice(options))
        selected.extend((key, {**cases[key], "hard_logic": marks[key][0]}, marks[key][1]) for key in selection)
    assert len({query["uid"] for _, query, _ in selected}) == 24
    return selected


def pick(rows, keys):
    seen = set()
    chosen = []
    for key in keys:
        for row in sorted(rows, key=key):
            if row[0] not in seen:
                chosen.append(row)
                seen.add(row[0])
                break
    return chosen


def ticket_record(row):
    return {"type": "train", "start_time": row["BeginTime"], "end_time": row["EndTime"],
            "duration_hours": row.get("Duration")}


def time_prune(obligations, day, slot):
    if slot is None:
        return False
    now = (day - 1) * 1440 + clock(slot[1])
    return any(o["day"] == day and o["status"] == "required" and not future_meal_possible(o, now)
               for o in obligations)


def probe_routes(env, query, pairs, places):
    city = query["target_city"]
    tasks = []
    for _, go, arrival, _, _, departure, obligations, hotel in pairs:
        for day in sorted({arrival // 1440 + 1, query["days"]}):
            origin = go["To"] if day == arrival // 1440 + 1 or hotel is None else hotel["name"]
            for now in (540, 735, 795, 1050, 1155):
                absolute = (day - 1) * 1440 + now
                if absolute < arrival or absolute >= departure:
                    continue
                remaining = [o for o in obligations if o["day"] == day
                             and o["status"] == "required"
                             and future_meal_possible(o, absolute)]
                for poi, mode in product(places, ("walk", "taxi", "metro")):
                    tasks.append((origin, day, now, remaining, poi, mode))

    def simulate(frontload):
        calls, time_cuts, accepted, missing = 0, 0, [], 0
        for i, (origin, day, now, obligations, poi, mode) in enumerate(tasks):
            try:
                opens, closes = poi["opentime"], poi["endtime"]
                optimistic = attraction_slot(hhmm(now), opens, closes, 90)
            except (ValueError, TypeError):
                continue
            if frontload and time_prune(obligations, day, optimistic):
                time_cuts += 1
                continue
            calls += 1
            try:
                route = env.transportation.goto(city, origin, poi["name"], hhmm(now), mode)
                if not isinstance(route, list):
                    continue
                arrive = route[-1]["end_time"] if route else hhmm(now)
                if clock(arrive) < now:
                    raise AssertionError("Route returned a clock before departure")
                slot = attraction_slot(arrive, opens, closes, 90)
                if slot is not None and clock(slot[0]) - clock(arrive) <= 60 and not time_prune(obligations, day, slot):
                    accepted.append(i)
            except (ValueError, TypeError, KeyError, IndexError):
                missing += 1
        return calls, time_cuts, accepted, missing

    before, _, original, missing_before = simulate(False)
    after, cuts, changed, missing_after = simulate(True)
    if after + cuts != before or original != changed:
        raise AssertionError(f"Inconsistent route counts or transitions for {query['uid']}")
    wall = defaultdict(list)
    for treatment in (False, True, True, False):
        started = perf_counter()
        observed = simulate(treatment)
        wall[treatment].append(perf_counter() - started)
        if observed[2] != original:
            raise AssertionError(f"Reachable transition sequence changed for {query['uid']}")
    return {"probed_routes": before, "routes_after": after, "pre_route_cuts": cuts,
            "remaining_transitions": len(original), "route_errors_before": missing_before,
            "route_errors_after": missing_after, "route_seconds_before": median(wall[False]),
            "route_seconds_after": median(wall[True])}


def simple_meal_floor(obligations, cheapest_restaurant):
    return cheapest_restaurant * sum(o["kind"] in ("lunch", "dinner") and o["status"] == "required"
                                     for o in obligations)


def known_minimum_price(restaurants, city):
    prices = [number(p) for p in restaurants["price"]]
    if any(p is None for p in prices):
        return 0
    return min((activity_unit_price(price, city) for price in prices), default=0)


def evaluate_case(env, case, query, budget):
    city, source = query["target_city"], query["start_city"]
    days, people = query["days"], query["people_number"]

    def tickets(origin, dest, departure_day):
        rows = []
        for mode in ("train", "airplane"):
            frame = env.intercitytransport.select(origin, dest, mode)
            if frame is None or frame.empty:
                continue
            for row in frame.to_dict("records"):
                if number(row.get("Cost")) is None:
                    continue
                try:
                    interval = ticket_interval(ticket_record(row), departure_day)
                except (ValueError, TypeError):
                    continue
                if interval["end"] >= days * 1440:
                    continue
                rows.append((row["TrainID" if mode == "train" else "FlightID"], row, interval["start"], interval["end"]))
        return rows

    without_budget = {**query, "hard_logic": [c for c in query["hard_logic"] if not c.startswith("cost")]}
    go_rows = [g for g in tickets(source, city, 1) if not selection_reason(without_budget, {"go_transport": g[1]})]
    back_rows = tickets(city, source, days)
    if not go_rows or not back_rows:
        raise ValueError(f"No priced ticket catalogue for {case}")
    go_rows = pick(go_rows, (lambda g: g[1]["Cost"], lambda g: g[3], lambda g: abs(g[3]-720),
                             lambda g: -g[3], lambda g: abs(g[3]-1830)))
    hotels = []
    if days > 1:
        explicit_rooms = next((int(c.split("==")[1]) for c in query["hard_logic"] if c.startswith("rooms==")), None)
        for index, hotel in enumerate(env.accommodations.data[city].to_dict("records")):
            beds = number(hotel.get("numbed"))
            price = number(hotel.get("price"))
            if not beds or price is None:
                continue
            rooms = explicit_rooms or math.ceil(people / beds)
            if selection_reason(query, {"accommodation": hotel}, rooms):
                continue
            hotels.append((activity_unit_price(price, city) * rooms, index, hotel, rooms))
        hotels.sort(key=lambda h: h[:2])
        indexes = dict.fromkeys(round((len(hotels) - 1) * p) for p in (0, .1, .25, .5, .75, .9))
        all_hotels = [(h[2], h[3]) for h in hotels]
        hotels = [all_hotels[i] for i in indexes] if hotels else []
    else:
        hotels = [(None, None)]
        all_hotels = hotels
    cheapest_restaurant = known_minimum_price(env.restaurants.data[city], city)

    @lru_cache(maxsize=None)
    def meal_check(go_id, back_id, has_hotel):
        go, back = departures[go_id], returns[back_id]
        layout = {"itinerary": [{"day": 1, "activities": [ticket_record(go[1])]},
                                {"day": days, "activities": [ticket_record(back[1])]}]}
        obligations = meal_obligations(layout)
        hotel_days = range(go[3] // 1440 + 2, days + 1) if has_hotel else ()
        rejected, food = meal_lower_bound(obligations, env.restaurants.data[city],
                                         go[3], back[2], hotel_days, city)
        return rejected, food, obligations

    departures = {g[0]: g for g in go_rows}
    returns = {b[0]: b for b in back_rows}
    counts, pairs, examples, budget_edges = Counter(), [], [], []
    for go_id, go, _, arrival in go_rows:
        eligible = [b for b in back_rows if b[2] > arrival and not selection_reason(
            without_budget, {"go_transport": go, "back_transport": b[1]})]
        selected_back = pick(eligible, (lambda b: b[1]["Cost"], lambda b: b[2],
                                       lambda b: abs(b[2] % 1440 - 690), lambda b: abs(b[2] % 1440 - 900),
                                       lambda b: abs(b[2] % 1440 - 1080), lambda b: -b[2]))
        for back_id, back, depart, _ in selected_back:
            witness = None
            nearest = None
            if budget is not None:
                for hotel, rooms in reversed(all_hotels):
                    combo = {"go_transport": go, "back_transport": back}
                    if hotel is not None:
                        combo["accommodation"] = hotel
                    cost = fixed_cost(query, combo, rooms)
                    if cost is not None and cost <= budget:
                        nearest = hotel, cost
                        break
            if nearest:
                hotel, cost = nearest
                rejected, food, obligations = meal_check(go_id, back_id, hotel is not None)
                simple_floor = simple_meal_floor(obligations, cheapest_restaurant)
                if not rejected:
                    entry = {"go": go_id, "back": back_id, "hotel": hotel["name"] if hotel else None,
                             "slack": budget - cost, "fixed": cost, "meal_floor": food * people,
                             "known_price_floor": simple_floor * people}
                    entry["base_meal_cut"] = bool(exceeded(query, cost + food * people))
                    entry["validated_price_cut"] = bool(exceeded(query, cost + max(food, simple_floor) * people))
                    budget_edges.append(entry)
            for hotel, rooms in hotels:
                counts["combinations"] += 1
                combo = {"go_transport": go, "back_transport": back}
                if hotel is not None:
                    combo["accommodation"] = hotel
                cost = fixed_cost(query, combo, rooms)
                if exceeded(query, cost):
                    counts["fixed_cost_cut"] += 1
                    continue
                rejected, food, obligations = meal_check(go_id, back_id, hotel is not None)
                if witness is None:
                    witness = (go_id, go, arrival, back_id, back, depart, obligations, hotel)
                if rejected:
                    counts["meal_window_cut"] += 1
                else:
                    known_price_floor = simple_meal_floor(obligations, cheapest_restaurant)
                    if exceeded(query, cost + people * food if cost is not None else None):
                        counts["meal_budget_cut"] += 1
                    if exceeded(query, cost + people * max(known_price_floor, food) if cost is not None else None):
                        counts["combined_price_cut"] += 1
                        examples.append({"go": go_id, "back": back_id, "hotel": hotel["name"] if hotel else None,
                                         "fixed": cost, "meal_floor": people * max(known_price_floor, food)})
            if witness:
                pairs.append(witness)
    if not pairs:
        # Isolate timing from the budget rule on one actual ticket pair when applicable.
        almost = [(g, b) for g in go_rows for b in back_rows if g[3] < b[2]]
        if almost:
            g, b = min(almost, key=lambda t: t[0][1]["Cost"] + t[1][1]["Cost"])
            hotel = hotels[0][0] if hotels else None
            _, _, obligations = meal_check(g[0], b[0], hotel is not None)
            pairs.append((g[0], g[1], g[3], b[0], b[1], b[2], obligations, hotel))
    pairs.sort(key=lambda p: p[5])
    export = env.attractions.data[city].to_dict("records")
    places = [export[i] for i in dict.fromkeys(round((len(export)-1)*i/9) for i in range(10))] if export else []
    route_variants = [pairs[i] for i in dict.fromkeys((0, len(pairs)//2, len(pairs)-1))] if pairs else []
    result = {"case": case, "uid": query["uid"], "tier": query["tag"], "days": days,
              "city": city, "budget": budget, "rules": query["hard_logic"],
              "combinations": counts["combinations"], "fixed_cost_cut": counts["fixed_cost_cut"],
              "meal_budget_cut": counts["meal_budget_cut"], "meal_window_cut": counts["meal_window_cut"],
              "combined_price_cut": counts["combined_price_cut"],
              "unique_meal_checks": meal_check.cache_info().misses, "meal_budget_examples": examples[:8],
              "budget_edge_pairs": len(budget_edges), "edge_base_cuts": sum(e["base_meal_cut"] for e in budget_edges),
              "edge_price_cuts": sum(e["validated_price_cut"] for e in budget_edges),
              "budget_edge_examples": sorted(budget_edges, key=lambda e: e["slack"])[:8]}
    return {**result, **probe_routes(env, query, route_variants, places)}


def check_saved_plans(env, baseline):
    from experiments.evaluation.common import plan_fingerprint
    from meal_pruning import preflight

    checked = []
    seen = set()
    roots = (baseline, ARTIFACTS / "EXP013_rsi_v3", PILOT,
             ARTIFACTS / "EXP008_all_tiers_1794457018569/run")
    for root in roots:
        for path in sorted(root.glob("**/shorttrip/profile/plan.json")):
            case = path.parents[2].name
            original = read(root / "queries" / f"{case}.json")
            plan = read(path)
            key = plan_fingerprint(plan)
            if key in seen or not plan.get("itinerary"):
                continue
            evidence = verify(path, original, True)
            if evidence["common"] != "pass" or evidence["C_audit"] != "pass":
                continue
            seen.add(key)
            city, start, days = original["target_city"], original["start_city"], original["days"]
            query = {**original, "hard_logic": program_constraints(original)[0]}
            activities = [a for day in plan["itinerary"] for a in day["activities"]]
            intercity = [a for a in activities if a["type"] in ("airplane", "train")]
            combo = {}
            for name, activity, origin, dest in zip(("go_transport", "back_transport"), intercity,
                                                     (start, city), (city, start)):
                key_name = "TrainID" if activity["type"] == "train" else "FlightID"
                data = env.intercitytransport.select(origin, dest, activity["type"])
                match = data.loc[data[key_name] == activity[key_name]]
                match = match.loc[(match["BeginTime"] == activity["start_time"]) & (match["EndTime"] == activity["end_time"])]
                combo[name] = match.iloc[0].to_dict()
            room_count = None
            stays = [a for a in activities if a["type"] == "accommodation"]
            if stays:
                a = stays[0]
                data = env.accommodations.data[city]
                combo["accommodation"] = data.loc[data["name"] == a["position"]].iloc[0].to_dict()
                room_count = a["rooms"]
            tickets = {"itinerary": [{"day": 1, "activities": [ticket_record(combo["go_transport"])]},
                                     {"day": days, "activities": [ticket_record(combo["back_transport"])]}]}
            obligations = meal_obligations(tickets)
            agent = SimpleNamespace(required_rooms=room_count, memory={"restaurants": env.restaurants.data[city]},
                                    requirements=lambda _: obligations)
            rejected = preflight(agent, query, combo)
            fixed = fixed_cost(query, combo, room_count)
            fee = simple_meal_floor(obligations, known_minimum_price(env.restaurants.data[city], city))
            rejected_with_price = exceeded(query, fixed + fee * query["people_number"]) if fixed is not None else None
            time_rejections = 0
            for day in plan["itinerary"]:
                done, last = set(), "00:00"
                for a in day["activities"]:
                    if a["type"] == "attraction":
                        data = env.attractions.data[city]
                        places = data.loc[data["name"] == a["position"]]
                        candidate = places.iloc[0]
                        when = min(clock(last), clock(a["start_time"]))
                        optimistic = attraction_slot(hhmm(when), candidate["opentime"], candidate["endtime"], 90)
                        pending = [o for o in obligations if o["kind"] not in done]
                        time_rejections += time_prune(pending, day["day"], optimistic)
                    done.add(a["type"])
                    last = a["end_time"]
            checked.append({"case": case, "uid": original["uid"], "source": str(path),
                            "preflight_rejection": rejected or rejected_with_price, "pre_route_rejections": time_rejections})
    return checked


def write_report(out, results, regressions):
    def aggregate(rows):
        keys = ("combinations", "fixed_cost_cut", "meal_budget_cut", "meal_window_cut", "probed_routes",
                "routes_after", "remaining_transitions", "route_seconds_before", "route_seconds_after")
        return [sum(r[k] for r in rows) for k in keys]

    groups = defaultdict(list)
    for row in results:
        groups[row["tier"]].append(row)
    report = ["# EXP015 分层前置规则测试", "",
              "24 条不同需求，每层 8 条。使用固定十城快照与官方已知约束的子集。"
              "组合和时间切片是受控探测。时间试验使用实际路线查询，共用原 C 组开闭馆、游览与用餐时间窗口。"
              "已过窗口的餐视为已安排。窗口内餐厅接驳的后置检查仍属于完整搜索。"
              "相同路线预热后按原、前置、前置、原顺序重复测时。", ""]
    report += table(["层次", "需求", "组合", "固定费用已排除", "追加餐费排除", "用餐窗口排除", "查路原→前置", "相同存活转移", "局部秒 原→前置"], (
        (tier, len(rows), combos, fixed, extra, meal, f"{n}→{m}", survivors, f"{before:.2f}→{after:.2f}")
        for tier, rows in [*((t, groups[t]) for t in TIERS), ("合计", results)]
        for combos, fixed, extra, meal, n, m, survivors, before, after in [aggregate(rows)]))
    report += ["原组与前置组逐条核对剩余转移的顺序。额外对已独立审核通过的历史行程检查两条规则。", ""]
    report += ["## 压预算边界", "",
               "边界组固定往返班次，从原酒店库选取固定费用最接近但不超过预算的组合。"
               "每班次对至多一个边界组合。午/晚餐的已知最小价格对未知营业时间同样成立。"
               "缺价时按零处理。两种下界取较大值，不重复加餐费。", ""]
    report += table(["层次", "普通组新增餐费排除", "边界组合", "原餐费预筛排除", "加已知价格下界后排除"], (
        (tier, sum(r["combined_price_cut"] for r in rows), sum(r["budget_edge_pairs"] for r in rows),
         sum(r["edge_base_cuts"] for r in rows), sum(r["edge_price_cuts"] for r in rows))
        for tier, rows in ((tier, groups[tier]) for tier in TIERS)))
    report += table(["案例", "组合数", "固定费用排除", "追加餐费排除", "用餐窗口排除", "省查路线", "路线原数"],
                    ((r["case"], r["combinations"], r["fixed_cost_cut"], r["meal_budget_cut"], r["meal_window_cut"],
                      r["pre_route_cuts"], r["probed_routes"]) for r in results))
    report += table(["历史合法行程", "预算和用餐预筛拒绝", "前置时间误拒"],
                    ((r["case"], r["preflight_rejection"], r["pre_route_rejections"]) for r in regressions))
    (out / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    (out / "RESULTS.json").write_text(json.dumps({"cases": results, "regressions": regressions},
                                                ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    out = parser.parse_args().out.resolve()
    source = PILOT / "sources/shorttrip"
    sys.path[:0] = [str(source), str(source / "experiments/c_only")]
    os.environ.pop("CHINATRAVEL_ZH_DATABASE", None)
    os.chdir(source)
    from chinatravel.environment.world_env import WorldEnv
    from chinatravel.agent.nesy_agent import llm_driven_rec

    # Match the frozen planner's fixed attraction duration rather than a heuristic estimate.
    import inspect
    method = ast.parse(dedent(inspect.getsource(llm_driven_rec.LLMDrivenAgent.select_poi_time)))
    first = method.body[0].body[0]
    if not isinstance(first, ast.Return) or not isinstance(first.value, ast.Constant) or first.value.value != 90:
        raise ValueError("Attraction visit duration changed in the benchmark source")
    env = WorldEnv(lang="zh")
    selected = select_queries()
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"cases": [case for case, _, _ in selected], "seed": 20261116,
                "source": str(source), "script_sha256": digest(Path(__file__).resolve()),
                "query_sha256": {case: digest(DATASET / f"{case}.json") for case, _, _ in selected}}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    with (out / "cases.jsonl").open("x", encoding="utf-8") as stream:
        for case, query, budget in selected:
            row = evaluate_case(env, case, query, budget)
            results.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(case, row["combinations"], row["meal_budget_cut"], row["probed_routes"], row["pre_route_cuts"], flush=True)
    os.chdir(PROJECT)
    baseline = ARTIFACTS / "EXP011_speed_20261114/baseline_live"
    regressions = check_saved_plans(env, baseline)
    write_report(out, results, regressions)


if __name__ == "__main__":
    os.environ.pop("CHINATRAVEL_ZH_DATABASE", None)
    sys.path[:0] = [str(PILOT / "sources/shorttrip"), str(PILOT / "sources/shorttrip/experiments/c_only")]
    from chinatravel.agent.nesy_agent.pruning import exceeded, fixed_cost, number, selection_reason
    from chinatravel.agent.nesy_agent.utils import activity_unit_price
    from rules import attraction_slot, clock, future_meal_possible, hhmm, meal_obligations, ticket_interval
    from meal_pruning import meal_lower_bound

    main()
