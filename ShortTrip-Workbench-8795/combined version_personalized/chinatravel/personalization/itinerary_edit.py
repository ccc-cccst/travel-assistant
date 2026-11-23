"""Trial a local itinerary change and publish it only after independent verification."""

from copy import deepcopy
from heapq import nsmallest

from .cli import read, save
from .integrated_planning import _card
from .meal_refinement import SNACK_OR_DRINK, _matches, _meal_goals, _route_minutes, _try_meal
from .plan_assessment import evaluate_archive
from .planning_compat import check_snapshot_reference, constructor_input, prepare_snapshot_routes
from .sketch_planning import _digest, _rebuild_day, optimize_day
from .trip_world import matches_tags, preferred_tag_terms
from chinatravel.planning.engine import Constructor, SearchBudgetExpired, clock, minutes, total_cost

INTERCITY = {"train", "airplane"}
MEALS = {"breakfast", "lunch", "dinner"}
EDIT_MODES = {"walk", "taxi", "metro"}
VISIT_PERIODS = {"morning": (480, 720), "afternoon": (720, 1080), "evening": (1080, 1320)}
MEAL_TRANSFER_TOLERANCE = 5


def _arrival_window(activity, appointments):
    kind, start = activity["type"], activity["start_time"]
    target = activity["start"] if kind in INTERCITY else activity["position"]
    buffer = {"train": 60, "airplane": 120}.get(kind, 0)
    suffix = "返程进站" if buffer else "预约" if (target, start) in appointments else "开始"
    return target, minutes(start) - buffer, f"「{target}」{suffix}"


def _previous_stop(plan, day, index, original):
    if index:
        previous = plan["itinerary"][day - 1]["activities"][index - 1]
        name = previous["end"] if previous["type"] in INTERCITY else previous["position"]
        return name, previous["end_time"]
    if day > 1:
        previous = plan["itinerary"][day - 2]["activities"][-1]
        route = original["transports"]
        return previous["position"], route[0]["start_time"] if route else original["start_time"]
    return None, None


def _recommend_meals(plan, day, index, traveler, goals, appointments, cuisine=None):
    activities = plan["itinerary"][day - 1]["activities"]
    meal = activities[index]
    if index + 1 >= len(activities):
        return []
    origin, earliest = _previous_stop(plan, day, index, meal)
    if origin is None:
        return []
    following = activities[index + 1]
    target, latest, _ = _arrival_window(following, appointments)
    begin, end = map(minutes, (meal["start_time"], meal["end_time"]))
    original_legs = meal["transports"] + following["transports"]
    original_minutes = _route_minutes(original_legs)
    original_cost = meal["cost"] + sum(leg["cost"] for leg in original_legs)
    current_total = sum(total_cost(entry["activities"]) for entry in plan["itinerary"])
    budget_room = traveler.request.get("budget", float("inf")) - current_total

    def proximity(row):
        name = row["name"]
        return (-len(_matches(row, goals)), traveler.distance(origin, name) + traveler.distance(name, target), name)

    matches_cuisine = _cuisine_filter(cuisine, plan["target_city"])
    pool = nsmallest(24, (
        row for row in traveler.food if row["name"] != meal["position"]
        and str(row.get("cuisine", "")).strip().casefold() not in SNACK_OR_DRINK
        and matches_cuisine(row.get("cuisine", ""))
    ), key=proximity)
    options = []
    for row in pool:
        try:
            traveler.check()
            choice = next(_try_meal(traveler, row, meal["type"], origin, target,
                                    minutes(earliest), latest, end - begin, fixed_start=begin), None)
        except SearchBudgetExpired:
            break
        if choice is None:
            continue
        visit, onward = choice
        legs = visit["transports"] + onward
        extra_cost = round(visit["cost"] + sum(leg["cost"] for leg in legs) - original_cost, 2)
        if extra_cost > budget_room + .005:
            continue
        duration = _route_minutes(legs)
        walking = _route_minutes(legs, "walk")
        hits = len(_matches(row, goals))
        options.append({"name": row["name"], "price": row["price"], "minutes": duration,
                        "extra_minutes": duration - original_minutes, "walking_minutes": walking,
                        "extra_cost": extra_cost, "matches": hits})
    return sorted(options, key=lambda row: (-row["matches"], row["minutes"], row["walking_minutes"],
                                            row["extra_cost"], row["name"]))[:8]


def _cuisine_filter(cuisine, city):
    if not cuisine:
        return lambda _: True
    exact, literal = preferred_tag_terms("cuisine", [cuisine], city)
    return lambda value: matches_tags([value], exact, literal)


def _meal_change_detail(before, after, index, origin, appointments):
    links = [{"from": origin, "to_before": before[index]["position"], "to": after[index]["position"],
              "before": _route_minutes(before[index]["transports"]),
              "after": _route_minutes(after[index]["transports"])}]
    if index + 1 < len(after):
        following = after[index + 1]
        target, _, _ = _arrival_window(following, appointments)
        links.append({"from_before": before[index]["position"], "from": after[index]["position"],
                      "to": target, "before": _route_minutes(before[index + 1]["transports"]),
                      "after": _route_minutes(following["transports"])})
    for pos in range(index + 1, len(after)):
        activity = after[pos]
        target, deadline, label = _arrival_window(activity, appointments)
        if activity["type"] not in INTERCITY and (target, activity["start_time"]) not in appointments:
            continue
        def arrival(items):
            route = items[pos]["transports"]
            return minutes(route[-1]["end_time"] if route else items[pos - 1]["end_time"])
        return {"connections": links, "anchor": {"label": label, "deadline": clock(deadline),
                    "before_minutes": deadline - arrival(before), "after_minutes": deadline - arrival(after),
                    "arrival": clock(arrival(after))}}
    return {"connections": links}


def verified_base(source, candidate_id):
    """Return the exact published candidate and its independent assessment."""
    def optional(name):
        path = source / name
        return read(path) if path.is_file() else {}

    final = optional("PLAN_VIEW.json")
    first = optional("WORKBENCH_FIRST.json")
    if first.get("candidate", {}).get("id") == candidate_id and first["assessment"]["validation_report"]["passed"]:
        return first["candidate"], first["assessment"]
    if not any(card["id"] == candidate_id for card in final.get("candidates", [])):
        raise ValueError("请选择已核验并发布的候选方案。")
    assessment = optional("PLAN_ASSESSMENT.json")
    checked = next((c for c in assessment.get("candidates", []) if c["id"] == candidate_id), None)
    if not checked or not checked["validation_report"]["passed"]:
        raise ValueError("原方案缺少已经通过的独立核验记录。")
    selected = next(c for c in read(source / "CANDIDATE_ARCHIVE.json")["archive"] if c["id"] == candidate_id)
    return selected, checked


def _next_anchor(activities, start, appointments):
    for i in range(start, len(activities)):
        item = activities[i]
        name, deadline, label = _arrival_window(item, appointments)
        if item["type"] not in INTERCITY and (name, item["start_time"]) not in appointments:
            continue
        route = item["transports"]
        if not route and i == 0:
            return None
        arrival = minutes(route[-1]["end_time"] if route else activities[i - 1]["end_time"])
        return {"label": label, "deadline": clock(deadline), "arrival": clock(arrival),
                "after_minutes": deadline - arrival}
    return None


def _insert_visit(plan, day, operation, traveler, facts):
    period = VISIT_PERIODS.get(operation.get("period"))
    duration = operation.get("duration")
    if not period or type(duration) is not int or not 15 <= duration <= 240:
        raise ValueError("请选择游览时段和 15–240 分钟的停留时间。")
    found = facts.place(traveler.city, operation["place"], "attraction")
    if found["status"] != "found":
        raise ValueError("无法唯一确认这个景点。")
    record, = found["records"]
    name, price = record["name"], record["price"]["value"]
    if not record["planning_catalog"] or not record["routable_in_snapshot"] or price is None:
        raise ValueError("这个景点缺少可核验的门票或接驳资料。")
    if any(a.get("position") == name and a["type"] == "attraction"
           for entry in plan["itinerary"] for a in entry["activities"]):
        raise ValueError("这个景点已在行程中，可以在时间线上调整游览时间。")
    if name in traveler.request.get("excluded_attractions", ()):
        raise ValueError("这个景点与已确认的排除要求冲突。")
    windows = [(max(lo, period[0]), min(hi, period[1])) for lo, hi in record["opening"]["windows_minutes"]]
    if not any(hi - lo >= duration for lo, hi in windows):
        raise ValueError("所选时段没有足够长的已知开放时间，可更换时段或缩短停留。")
    activities = plan["itinerary"][day - 1]["activities"]
    appointments = {(a["place"], a["start_time"]) for a in traveler.request["appointments"] if a["day"] == day}
    total = sum(total_cost(entry["activities"]) for entry in plan["itinerary"])
    best = None
    over_budget = False
    for i, following in enumerate(activities):
        origin, earliest = _previous_stop(plan, day, i, following)
        if origin is None:
            continue
        target, deadline, _ = _arrival_window(following, appointments)
        depart = minutes(earliest)
        if depart + duration > min(deadline, period[1]) or period[0] + duration > deadline:
            continue
        for _, arrival, incoming in traveler.routes_to(origin, name, depart, min(period[1], deadline) - duration, "local"):
            for lo, hi in windows:
                start = max(lo, arrival)
                end = start + duration
                if end > min(hi, deadline):
                    continue
                for _, reached, outgoing in traveler.routes_to(name, target, end, deadline, "local"):
                    old = following["transports"]
                    extra = price * traveler.people + sum(s["cost"] for s in incoming + outgoing) - sum(s["cost"] for s in old)
                    if traveler.request.get("budget") is not None and total + extra > traveler.request["budget"] + .005:
                        over_budget = True
                        continue
                    score = (_route_minutes(incoming + outgoing) - _route_minutes(old), extra, start, reached)
                    if best is None or score < best[0]:
                        best = score, i, traveler.poi_activity({"name": name, "price": price}, "attraction", start, end, incoming), outgoing
    if best is None:
        raise ValueError("可衔接的安排超过整团预算。" if over_budget else
                         "计入往返接驳后，所选时段没有足够空档。其他活动、预约和返程保留原时刻，可缩短停留或换一天。")
    _, i, visit, outgoing = best
    before_anchor = _next_anchor(activities, i, appointments)
    activities[i]["transports"] = outgoing
    activities.insert(i, visit)
    return _visit_change("insert_visit", visit, day, activities, i + 1, appointments, before_anchor)


def _visit_change(action, visit, day, activities, following, appointments, before_anchor):
    anchor = _next_anchor(activities, following, appointments)
    if anchor and before_anchor:
        anchor["before_minutes"] = before_anchor["after_minutes"]
    return {"action": action, "day": day, "name": visit["position"], "time": [visit["start_time"], visit["end_time"]],
            "anchor": anchor}


def _replace_stay(plan, day, old_name, name, traveler, facts):
    place = facts.place(traveler.city, name, "accommodation")
    if place["status"] != "found":
        raise ValueError("无法唯一确认这家酒店，请选择目录中的具体地点。")
    name = place["records"][0]["name"]
    hotel = next((row for row in traveler.hotels if row["name"] == name), None)
    if hotel is None:
        raise ValueError("这家酒店缺少可核验的房价、接驳资料，或与原住宿要求冲突。")
    if name == old_name:
        raise ValueError("请选择另一家酒店。")
    days = plan["itinerary"]
    stays = {entry["day"]: activity for entry in days for activity in entry["activities"]
             if activity["type"] == "accommodation" and activity["position"] == old_name}
    first = last = day
    while first - 1 in stays:
        first -= 1
    while last + 1 in stays:
        last += 1
    price, rooms = hotel["price"], hotel["_rooms"]
    for number in range(first, last + 1):
        stay = stays[number]
        stay.update(position=name, price=price, rooms=rooms, cost=round(price * rooms, 2))
        if hotel["_bed_count"] is None:
            stay.pop("room_type", None)
        else:
            stay["room_type"] = hotel["_bed_count"]
    # The morning after the final changed night also starts at the new hotel.
    for number in range(first, min(last + 1, len(days)) + 1):
        entry = days[number - 1]
        previous = days[number - 2]["activities"][-1]["position"] if number > 1 else None
        locked = {(a["position"], a["start_time"]) for a in entry["activities"] if a["type"] == "attraction"}
        order = tuple(range(len(entry["activities"])))
        rebuilt = _rebuild_day(traveler, entry, order, previous, locked, facts, prefer_cheaper=False)
        if rebuilt is None:
            raise ValueError(f"更换后，第 {number} 天的酒店接驳无法赶上原定安排。")
        days[number - 1] = rebuilt
    return {"old_hotel": old_name, "new_hotel": name, "day": first, "last_day": last}


def _replace_local(activity, name, plan, request, facts):
    kind = "restaurant" if activity["type"] in MEALS else "attraction"
    result = facts.place(plan["target_city"], name, kind)
    if result["status"] != "found":
        raise ValueError(f"无法唯一确认「{name}」，请选择目录中的具体地点。")
    record, = result["records"]
    price = record["price"]["value"]
    if not record["planning_catalog"] or not record["routable_in_snapshot"] or price is None:
        raise ValueError(f"「{name}」缺少接驳或价格资料。")
    count = request["people_number"] if kind == "restaurant" else activity["tickets"]
    activity.update(position=record["name"], price=price, cost=round(price * count, 2))


def _batch_connections(plan, day, originals, remaining, modes, appointments, traveler):
    activities = plan["itinerary"][day - 1]["activities"]
    allowed = traveler.request.get("local_modes", EDIT_MODES)
    for pos, slot in enumerate(remaining):
        traveler.check()
        activity, original = activities[pos], originals[slot]
        previous = activities[pos - 1] if pos else None
        former_previous = originals[slot - 1] if slot else None
        mode = modes.get(slot)
        changed = any(activity.get(key) != original.get(key) for key in ("position", "start_time"))
        changed_previous = any((previous or {}).get(key) != (former_previous or {}).get(key)
                               for key in ("position", "end_time", "end"))
        if not (changed or changed_previous or mode):
            continue
        origin, depart = _previous_stop(plan, day, pos, original)
        target, deadline, label = _arrival_window(activity, appointments)
        if origin is None:
            raise ValueError("找不到受影响活动前的本地接驳。")
        earliest = minutes(depart)
        if earliest > deadline:
            raise ValueError(f"最早 {depart} 出发，已晚于{label}的 {clock(deadline)}。")
        old = original.get("transports") or []
        if (not mode and old and old[0]["start"] == origin and old[-1]["end"] == target
                and earliest <= minutes(old[0]["start_time"]) and minutes(old[-1]["end_time"]) <= deadline):
            continue
        if mode:
            legs = traveler.route(origin, target, earliest, mode) or []
            if (not any(leg["mode"] == mode for leg in legs)
                    or any(leg["mode"] not in allowed for leg in legs)
                    or minutes(legs[-1]["end_time"]) > deadline):
                raise ValueError(f"所选交通方式无法在 {clock(deadline)} 前到达{label}。")
        else:
            choices = traveler.routes_to(origin, target, earliest, deadline, "local")
            if not choices:
                raise ValueError(f"从「{origin}」出发无法在 {clock(deadline)} 前到达{label}。")
            legs = choices[0][2]
        activity["transports"] = legs


def _pace_load(activities, period):
    start, end = VISIT_PERIODS.get(period, (0, 1440))

    def overlap(item):
        return max(0, min(end, minutes(item["end_time"])) - max(start, minutes(item["start_time"])))

    return sum(
        (overlap(activity) if activity["type"] == "attraction" else 0)
        + sum(overlap(leg) for leg in activity["transports"] if leg["mode"] in EDIT_MODES)
        for activity in activities)


def _batch_preview(plan, day, edits, pace, period, request, facts, traveler, publish):
    entry = plan["itinerary"][day - 1]
    originals = deepcopy(entry["activities"])
    appointments = {(a["place"], a["start_time"]) for a in request["appointments"] if a["day"] == day}
    modes, removed, meal = {}, set(), None
    for edit in edits:
        slot, action = edit["slot"] - 1, edit["action"]
        activity = entry["activities"][slot]
        kind, name = activity["type"], activity.get("position")
        locked = kind in INTERCITY or (name, activity["start_time"]) in appointments
        if action == "transport":
            if (not activity.get("transports")
                    or edit["mode"] not in request.get("local_modes", EDIT_MODES)):
                raise ValueError("所选接驳方式与原行程的交通要求冲突。")
            modes[slot] = edit["mode"]
            continue
        if locked or kind not in MEALS | {"attraction"}:
            raise ValueError(f"「{name or activity.get('start')}」为固定安排，请保留原地点与时刻。")
        if action == "remove_visit":
            if kind != "attraction" or name in request.get("must_attractions", ()):
                raise ValueError(f"「{name}」是已确认的必去地点或用餐安排。")
            removed.add(slot)
        elif action == "time":
            if minutes(edit["start"]) >= minutes(edit["end"]):
                raise ValueError(f"「{name}」的结束时间需要晚于开始时间。")
            activity.update(start_time=edit["start"], end_time=edit["end"])
        elif action == "replace":
            _replace_local(activity, edit["place"], plan, request, facts)
        elif action == "meal_options" and kind in MEALS:
            meal = edit
        else:
            raise ValueError("无法识别这组活动修改。")

    remaining = [slot for slot in range(len(originals)) if slot not in removed]
    entry["activities"] = [entry["activities"][slot] for slot in remaining]
    if not entry["activities"] or (day < len(plan["itinerary"]) and remaining[-1] != len(originals) - 1):
        raise ValueError("请保留当天最后一处安排，避免影响次日的出发地点。")
    choices = [None]
    if meal:
        pos = remaining.index(meal["slot"] - 1)
        activity = entry["activities"][pos]
        origin, _ = _previous_stop(plan, day, pos, originals[remaining[pos]])
        following = entry["activities"][pos + 1] if pos + 1 < len(remaining) else None
        target = _arrival_window(following, appointments)[0] if following else origin
        if origin is None:
            raise ValueError("这餐之前缺少已核验的出发地点。")
        matches_cuisine = _cuisine_filter(meal.get("cuisine"), plan["target_city"])
        choices = [row["name"] for row in nsmallest(12, (
            row for row in traveler.food if row["name"] != activity["position"]
            and str(row.get("cuisine", "")).strip().casefold() not in SNACK_OR_DRINK
            and matches_cuisine(row.get("cuisine", ""))),
            key=lambda row: (traveler.distance(origin, row["name"]) + traveler.distance(row["name"], target),
                             row["name"]))]
        if not choices:
            raise ValueError("当前目录没有符合菜系要求的可选饭店。")

    error = None
    drafts = []
    pace_before = _pace_load(originals, period) if pace == "lighter" else None
    for name in choices:
        candidate = deepcopy(plan)
        activities = candidate["itinerary"][day - 1]["activities"]
        try:
            if name:
                _replace_local(activities[pos], name, candidate, request, facts)
            for slot, activity in zip(remaining, activities):
                if activity["type"] not in MEALS | {"attraction"}:
                    continue
                old = originals[slot]
                if all(activity[k] == old[k] for k in ("position", "start_time", "end_time")):
                    continue
                kind = "restaurant" if activity["type"] in MEALS else "attraction"
                place = facts.place(plan["target_city"], activity["position"], kind)
                windows = place["records"][0]["opening"]["windows_minutes"] if place["status"] == "found" else []
                begin, end = map(minutes, (activity["start_time"], activity["end_time"]))
                if not any(lo <= begin < end <= hi for lo, hi in windows):
                    raise ValueError(f"「{activity['position']}」的 {clock(begin)}–{clock(end)} 超出可核验的开放时段。")
            _batch_connections(candidate, day, originals, remaining, modes, appointments, traveler)
            pace_after = _pace_load(activities, period) if pace == "lighter" else None
            if pace_before is not None and pace_after >= pace_before:
                when = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}.get(period, "当天")
                raise ValueError(f"这次试排没有减少{when}的接驳或游览占用。可指定要缩短的活动。")
        except ValueError as excuse:
            error = excuse
            continue
        except SearchBudgetExpired:
            if drafts:
                break
            raise
        changes = []
        by_slot = dict(zip(remaining, activities))
        for slot, before in enumerate(originals):
            after = by_slot.get(slot)
            fields = ("position", "start_time", "end_time", "transports")
            if after and all(before.get(key) == after.get(key) for key in fields):
                continue
            def detail(item):
                if item is None:
                    return None
                legs = item["transports"]
                return {"name": item.get("position") or f"{item['start']} → {item['end']}",
                        "time": [item["start_time"], item["end_time"]],
                        "route_minutes": _route_minutes(legs),
                        "arrival": legs[-1]["end_time"] if legs else None,
                        "locked": item["type"] in INTERCITY or (item.get("position"), item["start_time"]) in appointments}
            changes.append({"before": detail(before), "after": detail(after),
                            "slot": remaining.index(slot) + 1 if after else None})
        if not changes:
            raise ValueError("调整后与当前行程相同。")
        nearby_minutes = sum(_route_minutes(item["transports"]) for item in activities[pos:pos + 2]) if meal else 0
        day_cost = total_cost(activities)
        locked = []
        for activity in activities:
            kind, at = activity["type"], activity["start_time"]
            name = activity.get("position")
            if (name, at) in appointments or (kind in INTERCITY and day == len(plan["itinerary"])):
                legs = activity["transports"]
                locked.append({"name": name or f"{activity['start']} → {activity['end']}", "start": at,
                               "arrival": legs[-1]["end_time"] if legs else None})
        change = {"action": "batch", "day": day, "changes": changes, "locks": locked}
        if pace_before is not None:
            change["pace"] = {"period": period, "before": pace_before, "after": pace_after}
        drafts.append((nearby_minutes, day_cost, candidate, change))
    if not drafts:
        raise error or ValueError("没有找到能满足这组要求的调整。")
    fastest = min(minutes for minutes, _, _, _ in drafts)

    def rank(draft):
        nearby_minutes, cost, _, _ = draft
        if nearby_minutes <= fastest + MEAL_TRANSFER_TOLERANCE:
            return (0, cost, nearby_minutes)
        return (1, nearby_minutes, cost)

    # Similar access times leave room to avoid an expensive restaurant by default.
    for _, _, candidate, change in sorted(drafts, key=rank):
        result = publish(candidate, change, probing=True)
        if result["state"] == "complete":
            return result
        error = ValueError(result["message"])
    raise error


def edit_preview(source, out, candidate_id, operation, env, facts):
    spec, snapshot = read(source / "request.json"), read(source / "WORLD_FACTS.json")
    check_snapshot_reference(spec, snapshot, facts)
    save(out / "request.json", spec)
    meta = {"protocol": "integrated_planning_v1", "stage": "itinerary_edit",
            "request_id": spec.get("request_id"), "request_revision": spec["request_revision"]}

    def finish(state, message, **detail):
        return save(out / "STATUS.json", {**meta, "state": state, "message": message, **detail})

    def conflict(message, **evidence):
        return finish("edit_conflict", message, reason_code="edit_conflict", evidence=evidence)

    finish("planning", "正在重新计算受影响的接驳并核验行程。")
    selected, before = verified_base(source, candidate_id)
    request, unsupported = constructor_input(spec, snapshot=snapshot)
    if unsupported:
        return conflict("原需求还有无法核验的条件。")

    def publish(plan, summary, *, probing=False):
        # Derived totals in the original response must follow the new activity and leg costs.
        if "total_cost" in plan:
            plan["total_cost"] = round(sum(total_cost(entry["activities"]) for entry in plan["itinerary"]), 2)
        edited = {"id": _digest(plan), "source": "workbench_edit", "itinerary": plan}
        archive = {"schema_version": "sketch_trial_v1", "snapshot_id": snapshot["snapshot_id"], "archive": [edited]}
        assessment = evaluate_archive(spec, snapshot, archive, env, facts)
        save(out / "PLAN_ASSESSMENT.json", assessment)
        check, = assessment["candidates"]
        if not check["validation_report"]["passed"]:
            report = check["validation_report"]
            labels = [r.get("label") or r.get("field") for r in report["hard_requirements"] if r["status"] != "passed"]
            detail = ("涉及 " + "、".join(dict.fromkeys(filter(None, labels))) if labels else
                      next((issue["message"] for issue in report["issues"] if issue.get("message")), "请检查该日时刻与接驳"))
            message = "修改后的行程未通过核验。" + detail + "。"
            if probing:
                return {"state": "edit_conflict", "message": message}
            return conflict(message, issues=report["issues"])
        edited["places"] = check["places"]
        save(out / "WORLD_FACTS.json", snapshot)
        save(out / "CANDIDATE_ARCHIVE.json", archive)
        save(out / "PLAN_VIEW.json", {"verified_count": 1, "recommended_id": edited["id"],
                                     "alternatives": [], "candidates": [_card(edited, check, spec, facts)]})
        return finish("complete", "修改预览已通过核验。", edit={
            **summary, "before": before["quality_vector"], "after": check["quality_vector"]})

    plan = deepcopy(selected["itinerary"])
    day, action = operation["day"], operation["action"]
    if type(day) is not int or not 1 <= day <= len(plan["itinerary"]):
        return conflict("请选择原方案中的行程日。")
    activities = plan["itinerary"][day - 1]["activities"]
    if action == "batch":
        prepare_snapshot_routes(request, env, facts)
        try:
            return _batch_preview(plan, day, operation["operations"], operation.get("pace"), operation.get("pace_period"),
                                  request, facts,
                                  Constructor(request, env, 8, 1), publish)
        except ValueError as error:
            return conflict(str(error))
        except SearchBudgetExpired:
            return finish("search_timeout", "组合修改已达到接驳复核时限，请缩小调整范围后重试。")
    if action == "insert_visit":
        prepare_snapshot_routes(request, env, facts)
        try:
            change = _insert_visit(plan, day, operation, Constructor(request, env, 8, 1), facts)
        except ValueError as error:
            return conflict(str(error))
        except SearchBudgetExpired:
            return finish("search_timeout", "新增景点的接驳复核超时，可缩短停留或重试。")
        return publish(plan, change)
    if action == "optimize":
        result = optimize_day(spec, snapshot, plan, day, env, facts, prefer_faster=True)
        if result["state"] != "improved":
            return finish("no_route_improvement", "本次没有找到接驳更短且通过核验的新顺序，可继续使用当前安排。")
        after = result["itinerary"]["itinerary"][day - 1]["activities"]
        return publish(result["itinerary"], {
            "action": action, "day": day,
            "original_order": [a["position"] for a in activities if a["type"] not in INTERCITY],
            "order": [a["position"] for a in after if a["type"] not in INTERCITY],
        })
    slot = operation["slot"]
    if type(slot) is not int or not 1 <= slot <= len(activities):
        return conflict("原方案中的活动编号已失效。")
    index = slot - 1
    activity = activities[index]
    original = activity.copy()
    appointments = {(a["place"], a["start_time"]) for a in request["appointments"] if a["day"] == day}

    if action == "remove_visit":
        if (activity["type"] != "attraction" or (activity["position"], activity["start_time"]) in appointments
                or activity["position"] in request.get("must_attractions", ())):
            return conflict("只能移除自由游览的景点，已确认的必去地点、预约和返程需要保留。")
        if index + 1 >= len(activities):
            return conflict("缺少下一站，无法复核移动后的接驳。")
        before_anchor = _next_anchor(activities, index + 1, appointments)
        origin, earliest = _previous_stop(plan, day, index, activity)
        following = activities[index + 1]
        target, deadline, label = _arrival_window(following, appointments)
        if origin is None:
            return conflict("无法确认删去这一站后的出发地点。")
        prepare_snapshot_routes(request, env, facts)
        try:
            routes = Constructor(request, env, 8, 1).routes_to(origin, target, minutes(earliest), deadline, "local")
        except SearchBudgetExpired:
            return finish("search_timeout", "移除景点后的接驳复核超时，请重试。")
        if not routes:
            return conflict(f"移除后查不到能按时到达{label}的接驳。")
        following["transports"] = routes[0][2]
        activities.pop(index)
        return publish(plan, _visit_change(action, activity, day, activities, index, appointments, before_anchor))

    if action == "advance_transfer":
        old = activity.get("transports") or []
        target, deadline, _ = _arrival_window(activity, appointments)
        returning = activity["type"] in INTERCITY and day == len(plan["itinerary"]) and index == len(activities) - 1
        if not index or not (returning or (target, activity["start_time"]) in appointments):
            return conflict("只可提前预约或返程前的接驳。")
        if not any(leg["mode"] == "metro" for leg in old):
            return conflict("原接驳没有地铁，请先核对交通方式。")
        origin, earliest = _previous_stop(plan, day, index, original)
        earlier = minutes(operation["start"])
        if not minutes(earliest) <= earlier < minutes(old[0]["start_time"]):
            return conflict("前一项活动结束后的空档不足以提前到这个时刻。")
        prepare_snapshot_routes(request, env, facts)
        traveler = Constructor(request, env, 8, 1)
        try:
            revised = traveler.route(origin, target, earlier, "metro") or []
        except SearchBudgetExpired:
            return finish("search_timeout", "提前接驳的本地复核超时。", reason_code="search_timeout")
        allowed = request.get("local_modes", EDIT_MODES)
        if (not revised or not any(s["mode"] == "metro" for s in revised)
                or any(s["mode"] not in allowed for s in revised)
                or revised[0]["start_time"] != operation["start"] or minutes(revised[-1]["end_time"]) > deadline):
            return conflict("指定时刻没有能在原定提前量内抵达的本地地铁接驳。")
        activity["transports"] = revised
        return publish(plan, {"action":action, "day":day, "slot":slot, "from":origin, "to":target,
                              "original_departure":old[0]["start_time"], "departure":revised[0]["start_time"],
                              "local_arrival":revised[-1]["end_time"], "deadline":clock(deadline)})

    if action == "meal_options":
        if activity["type"] not in MEALS or (activity["position"], activity["start_time"]) in appointments:
            return conflict("请选择可更换的用餐安排。")
        prepare_snapshot_routes(request, env, facts)
        choices = _recommend_meals(plan, day, index, Constructor(request, env, 8, 1),
                                   _meal_goals(snapshot, before), appointments, operation.get("cuisine"))
        return finish("complete", "已按当前行程查找饭店。", meal_options=choices)

    if action == "replace" and activity["type"] == "accommodation":
        prepare_snapshot_routes(request, env, facts)
        traveler = Constructor(request, env, 8, 1)
        try:
            change = _replace_stay(plan, day, activity["position"], operation["place"], traveler, facts)
        except ValueError as error:
            return conflict(str(error))
        except SearchBudgetExpired:
            return finish("search_timeout", "酒店接驳复核达到时间上限，请稍后重试。", reason_code="search_timeout")
        return publish(plan, {**change, "action": action})

    if action in {"time", "replace"}:
        if activity["type"] != "attraction" and activity["type"] not in MEALS:
            return conflict("请选择一项景点或用餐活动。")
        if (activity["position"], activity["start_time"]) in appointments:
            return conflict(f"「{activity['position']}」预约已锁定在 {activity['start_time']}。")
    if action == "time":
        try:
            begin, end = minutes(operation["start"]), minutes(operation["end"])
        except (ValueError, KeyError):
            return conflict("请填写有效的游览起止时刻。")
        if begin >= end:
            return conflict("游览结束时刻需要晚于开始时刻。")
        activity.update(start_time=clock(begin), end_time=clock(end))
    elif action == "replace":
        kind = "restaurant" if activity["type"] in MEALS else "attraction"
        label = "饭店" if kind == "restaurant" else "景点"
        result = facts.place(plan["target_city"], operation["place"], kind)
        if result["status"] != "found":
            return conflict(f"无法唯一确认这处{label}，请选择目录中的具体地点。")
        record, = result["records"]
        price = record["price"]["value"]
        if not record["planning_catalog"] or not record["routable_in_snapshot"] or price is None:
            return conflict(f"这处{label}缺少接驳或价格资料，暂时无法核验。")
        if kind == "attraction" and record["name"] != original["position"] and any(
                v.get("position") == record["name"] and v["type"] == "attraction"
                for entry in plan["itinerary"] for v in entry["activities"]):
            return conflict(f"「{record['name']}」已经安排在这份行程中。")
        count = request["people_number"] if kind == "restaurant" else activity["tickets"]
        activity.update(position=record["name"], price=price, cost=round(price * count, 2))
    elif action == "transport":
        if operation["mode"] not in EDIT_MODES or not activity.get("transports"):
            return conflict("这段接驳无法采用所选交通方式。")
    else:
        raise ValueError("无法识别行程修改操作。")

    if action != "transport":
        if index + 1 < len(activities):
            _, deadline, label = _arrival_window(activities[index + 1], appointments)
            if minutes(activity["end_time"]) > deadline:
                return conflict(f"上一站最早 {activity['end_time']} 出发，已晚于{label}要求的 {clock(deadline)}。",
                                day=day, earliest_departure=activity["end_time"], deadline=clock(deadline))
        kind = "restaurant" if activity["type"] in MEALS else "attraction"
        record = facts.place(plan["target_city"], activity["position"], kind)
        begin, end = minutes(activity["start_time"]), minutes(activity["end_time"])
        windows = record["records"][0]["opening"]["windows_minutes"] if record["status"] == "found" else []
        if not any(lo <= begin < end <= hi for lo, hi in windows):
            return conflict(f"「{activity['position']}」的 {clock(begin)}–{clock(end)} 超出可核验的开放时段。",
                            opening_windows=windows)

    prepare_snapshot_routes(request, env, facts)
    traveler = Constructor(request, env, 8, 1)
    allowed = request.get("local_modes", ("metro", "taxi", "walk"))
    mode = operation.get("mode") if action == "transport" else None
    if mode and mode not in allowed:
        return conflict("所选交通方式与这份行程的交通要求冲突。")

    # Only incoming edges whose endpoint, time window, or requested mode changed need another route.
    affected = range(index, min(index + (1 if mode else 2), len(activities)))
    try:
        for i in affected:
            following = activities[i]
            target, deadline, label = _arrival_window(following, appointments)
            origin, earliest = _previous_stop(plan, day, i, original)
            if origin is None:
                return conflict("这项活动前没有可修改的本地接驳。")
            depart = minutes(earliest)
            if action == "replace" and operation.get("keep_connection_departure") and i == index + 1:
                previous_route = following["transports"]
                if not previous_route or (following["type"] not in INTERCITY and (target, following["start_time"]) not in appointments):
                    return conflict("无法确认预约或返程前的原计划出发时刻。")
                depart = minutes(previous_route[0]["start_time"])
                if depart < minutes(earliest):
                    return conflict("前一站结束时已超过原接驳的出发时刻。")
            if depart > deadline:
                return conflict(f"上一站最早 {earliest} 出发，已晚于{label}要求的 {clock(deadline)}。",
                                day=day, earliest_departure=earliest, deadline=clock(deadline))
            old = following["transports"]
            if (not mode and old and old[0]["start"] == origin and old[-1]["end"] == target
                    and depart <= minutes(old[0]["start_time"]) and minutes(old[-1]["end_time"]) <= deadline):
                continue
            if mode:
                forced = traveler.route(origin, target, depart, mode)
                options = [(0, minutes(forced[-1]["end_time"]), forced)] if forced and (
                    any(leg["mode"] == mode for leg in forced)
                    and all(leg["mode"] in allowed for leg in forced)) else []
            else:
                options = traveler.routes_to(origin, target, depart, 1439, "local")
            feasible = [option for option in options if option[1] <= deadline]
            if not feasible:
                arrival = min((r[1] for r in options), default=None)
                if arrival is not None:
                    return conflict(f"最早 {clock(arrival)} 到达，赶不上{label}要求的 {clock(deadline)}。",
                                    day=day, earliest_arrival=clock(arrival), deadline=clock(deadline))
                return conflict(f"快照中查不到「{origin}」到「{target}」的可核验路线。")
            following["transports"] = feasible[0][2]
    except SearchBudgetExpired:
        return finish("search_timeout", "接驳复核达到时间上限，请稍后重试。", reason_code="search_timeout")

    if plan == selected["itinerary"]:
        return conflict("调整后与当前行程相同。")
    detail = {}
    if action == "replace" and activity["type"] in MEALS:
        prior = selected["itinerary"]["itinerary"][day - 1]["activities"]
        origin, _ = _previous_stop(plan, day, index, original)
        detail = _meal_change_detail(prior, activities, index, origin, appointments)
    return publish(plan, {"action": action, "day": day, "slot": slot, "original_name": original.get("position"),
                          "name": activity.get("position"), "original_time": [original["start_time"], original["end_time"]],
                          "time": [activity["start_time"], activity["end_time"]], **detail})
