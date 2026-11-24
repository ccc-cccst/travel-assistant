"""Edit a verified plan without moving other stops or the confirmed return."""

from copy import deepcopy
from itertools import product

from .engine import Constructor, SearchBudgetExpired, clock, minutes
from .validation import INTERCITY, validate_plan


class EditConflict(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def lock_key(day, activity):
    return {"day": day, "place": activity["position"],
            "start_time": activity["start_time"], "end_time": activity["end_time"]}


def contains_locks(plan, locks):
    scheduled = [lock_key(day["day"], stop) for day in plan["itinerary"]
                 for stop in day["activities"] if stop["type"] == "attraction"]
    return all(lock in scheduled for lock in locks)


def activity_at(plan, day, index):
    if not (type(day) is int and type(index) is int and 1 <= day <= len(plan["itinerary"])):
        raise EditConflict("invalid_activity", "请选择要编辑的日期和景点。")
    activities = plan["itinerary"][day - 1]["activities"]
    if not (0 <= index < len(activities)) or activities[index]["type"] != "attraction":
        raise EditConflict("invalid_activity", "只能通过此入口编辑景点。")
    return activities[index]


def overlaps(begin, end, lock):
    return begin < minutes(lock["end_time"]) and minutes(lock["start_time"]) < end


def edit_visit(plan, spec, env, locks, day, index, place, start_time, end_time, seconds):
    current = activity_at(plan, day, index)
    if not isinstance(place, str) or not isinstance(start_time, str) or not isinstance(end_time, str):
        raise EditConflict("invalid_time", "请完整填写景点、开始和结束时间。")
    try:
        begin, end = minutes(start_time), minutes(end_time)
        if not 0 <= begin < end < 1440:
            raise ValueError
    except ValueError:
        raise EditConflict("invalid_time", "游览结束须晚于开始，且两者都须在当天。") from None
    current_lock = lock_key(day, current)
    if current_lock in locks:
        if place == current["position"] and begin == minutes(current["start_time"]) and end == minutes(current["end_time"]):
            return plan
        raise EditConflict("appointment_conflict", f"第 {day} 天 {current['start_time']}–{current['end_time']} 的"
                           f"「{current['position']}」已标记为确认预约，地点和时段已锁定。")
    for lock in locks:
        if day == lock["day"] and overlaps(begin, end, lock):
            raise EditConflict("appointment_conflict", f"新时段与第 {day} 天 {lock['start_time']}–{lock['end_time']} 的"
                               f"「{lock['place']}」确认预约重叠。")
    if place == current["position"] and begin == minutes(current["start_time"]) and end == minutes(current["end_time"]):
        return plan
    constructor = Constructor(spec, env, seconds, max_candidates=1)
    rows = [row for row in constructor.spots if row["name"] == place]
    if not rows:
        raise EditConflict("unknown_attraction", "该景点缺少可核验的地点、价格信息，或与你确认的条件冲突。")
    visiting_hours = []
    for row in rows:
        try:
            opens, closes = minutes(row["opentime"]), minutes(row["endtime"])
            if closes <= opens:
                closes += 1440
        except (KeyError, ValueError):
            continue
        visiting_hours.append((opens, closes, row))
    available = [row for opens, closes, row in visiting_hours if opens <= begin < end <= closes]
    if not available:
        times = "、".join(f"{clock(opens)}–{clock(closes)}" for opens, closes, _ in visiting_hours)
        message = f"「{place}」的本地营业时间为 {times}，请在该时段内安排。" if times else f"「{place}」缺少可核验的营业时间。"
        raise EditConflict("opening_hours", message)

    updated = deepcopy(plan)
    activities = updated["itinerary"][day - 1]["activities"]
    if index:
        previous = activities[index - 1]
        origin = previous.get("position") or previous["end"]
        earliest = minutes(previous["end_time"])
    else:
        origin = updated["itinerary"][day - 2]["activities"][-1]["position"]
        first_leg = current["transports"][0] if current["transports"] else current
        earliest = min(510, minutes(first_leg["start_time"]))
    if begin < earliest:
        raise EditConflict("previous_activity", f"开始太早，上一段行程最早 {clock(earliest)} 结束。")
    next_stop = activities[index + 1]
    returning = next_stop["type"] in INTERCITY
    next_place = next_stop["start"] if returning else next_stop["position"]
    next_locked = next_stop["type"] == "attraction" and lock_key(day, next_stop) in locks
    latest = minutes(next_stop["start_time"])
    if returning:
        latest -= 60 if next_stop["type"] == "train" else 120
    if next_stop["type"] == "accommodation":
        latest = 1320  # the existing planner's latest hotel check-in
    if end > latest and returning:
        raise EditConflict("return_conflict", f"须在 {clock(latest)} 前抵达「{next_place}」，以保留原返程的进站或登机时间。")
    find_outgoing = constructor.routes_to_appointment if next_locked else constructor.routes_to
    outgoing = find_outgoing(place, next_place, end, latest, "local") if end <= latest else []
    if not outgoing:
        code = "appointment_conflict" if next_locked else "return_conflict" if returning else "next_activity"
        subject = "确认预约" if next_locked else "返程接驳" if returning else "下一项安排"
        raise EditConflict(code, f"游览结束后无法在 {clock(latest)} 前到达「{next_place}」以保留{subject}。")
    incoming = constructor.routes_to(origin, place, earliest, begin, "local")
    if not incoming:
        raise EditConflict("inbound_transit", f"从「{origin}」出发，无法在 {clock(begin)} 前到达「{place}」。")

    best_issues = []
    try:
        for row, (_, _, route_in), (_, arrival, route_out) in product(available, incoming, outgoing):
            constructor.check()
            activities[index] = constructor.poi_activity(row, "attraction", begin, end, route_in)
            next_stop["transports"] = route_out
            if next_stop["type"] == "accommodation":
                next_stop["start_time"] = clock(max(arrival, minutes(plan["itinerary"][day - 1]["activities"][index + 1]["start_time"])))
            validation = validate_plan(updated, spec, env)
            if validation["passed"] and contains_locks(updated, locks):
                return updated
            issues = validation["issues"]
            if issues and (not best_issues or len(issues) < len(best_issues)):
                best_issues = issues
    except SearchBudgetExpired:
        raise EditConflict("edit_timeout", "调整接驳超过本次计算时限，可以缩短时段或稍后重试。") from None
    message = "；".join(issue["message"] for issue in best_issues[:2]) or "调整后的行程未通过预约或路线核验。"
    raise EditConflict("validation_failed", message)

