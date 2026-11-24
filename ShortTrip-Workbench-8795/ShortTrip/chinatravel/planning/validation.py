"""Independent final checks for the same-day-ticket planning protocol.

Constructor flags are not evidence: prices come from the supplied environment,
and mandatory visits, nights, meals and timelines are recomputed from the plan.
"""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import math
from pathlib import Path
import re

from jsonschema import Draft7Validator
from chinatravel.soft_constraints.scoring import clock
from .snapshot_constraints import required_features_present, ticket_matches

SCHEMA = json.loads((Path(__file__).parents[1] / "evaluation/output_schema.json").read_text(encoding="utf-8"))
INTERCITY = {"train", "airplane"}
MEALS = {"breakfast", "lunch", "dinner"}
# An hour for meals; native dining evidence requires starting before 14/20.
MEAL_WINDOWS = {"lunch": (660, 840, 900), "dinner": (1020, 1200, 1260)}


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _same_price(actual, expected):
    try:
        expected = float(expected)
    except (TypeError, ValueError):
        return False
    return _number(actual) and _number(expected) and abs(actual - expected) <= .01


def _commonsense_issues(request, plan):
    """Retain grounded routes/station aliases and installed-data checks."""
    issues = []
    try:
        from chinatravel.symbol_verification import commonsense_constraint as common
        common._set_tool_lang("zh")
        for name in ("Is_activity_grounded", "Is_intercity_transport_correct", "Is_attractions_correct",
                     "Is_hotels_correct", "Is_restaurants_correct", "Is_transport_correct",
                     "Is_time_correct", "Is_space_correct"):
            if name == "Is_hotels_correct" and request.get("reference_lodging"):
                # The checks below already verify each hotel, its price, rooms and nights.
                continue
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                table, messages = getattr(common, name)(request, plan, verbose=False)
            if not all(float(v) == 0 for v in table.iloc[0]):
                issues.append({"code": name, "message": str(messages)})
    except Exception as error:
        issues.append({"code": "verification_unavailable", "message": f"{type(error).__name__}: {error}"})
    return issues


def validate_plan(plan, request, env):
    if hasattr(request, "to_dict"):
        request = request.to_dict()
    errors = list(Draft7Validator(SCHEMA).iter_errors(plan))
    if errors:
        return {"passed": False, "issues": [{"code": "schema", "message": e.message} for e in errors[:6]]}
    issues = []

    def require(ok, code, message):
        if not ok:
            item = {"code": code, "message": message}
            if item not in issues:
                issues.append(item)

    require(not any(i.get("blocking", False) for i in request.get("issues", [])),
            "unresolved_request", "需求仍有未确认的硬条件")
    appointments = request.get("appointments") or []
    require(not request.get("reservations") and (
        all(item.get("confirmed") for item in appointments) if appointments else
        not re.search(r"预约|订好|已订|预订了", request.get("nature_language", ""))),
        "unsupported_appointment", "预约地点和时段需要确认")
    days, people = request.get("days"), request.get("people_number")
    if (not isinstance(days, int) or isinstance(days, bool) or days < 1 or
            not isinstance(people, int) or isinstance(people, bool) or people < 1):
        return {"passed": False, "issues": [{"code": "request", "message": "天数和人数应为正整数"}]}
    for field in ("start_city", "target_city", "people_number"):
        require(plan.get(field) == request.get(field), "identity", field + " 与需求不一致")
    require([d["day"] for d in plan["itinerary"]] == list(range(1, days + 1)),
            "days", "旅行日期与需求不一致")
    acts = [(d["day"], a) for d in plan["itinerary"] for a in d["activities"]]
    for item in appointments:
        if item.get("confirmed"):
            require(any(day == item["day"] and activity["type"] == "attraction" and
                        (activity.get("position"), activity["start_time"], activity["end_time"]) ==
                        (item["place"], item["start_time"], item["end_time"]) for day, activity in acts),
                    "appointment_time", "必须保留已确认的预约时段和地点 " + item["place"])
    tickets = [(day, a) for day, a in acts if a["type"] in INTERCITY]
    require(len(tickets) == 2, "roundtrip", "需要完整的去程和返程")
    if (any(request.get(key) for key in ("required_hotel_features", "snapshot_room_occupancy",
                                         "requires_lodging", "required_hotel_names"))
            or any(request.get(key) is not None for key in ("hotel_price_limit", "hotel_party_price_limit"))):
        require(any(a["type"] == "accommodation" for _, a in acts),
                "required_lodging", "需要住宿才能满足指定的房型或酒店设施")
    if len(tickets) == 2:
        require(acts and acts[0] == tickets[0] and acts[-1] == tickets[1]
                and tickets[0][0] == 1 and tickets[1][0] == days,
                "roundtrip_position", "去程应是第一天首项，返程应是最后一天末项")

    city = request.get("target_city")
    catalogs = {}
    for kind in ("attractions", "restaurants", "accommodations"):
        frame = getattr(getattr(env, kind, None), "data", {}).get(city)
        catalogs[kind] = [] if frame is None else frame.to_dict("records")
    visited = {"attractions": set(), "restaurants": set()}
    categories = {"attraction_types": set(), "restaurant_types": set()}
    total, cost_complete, source_tickets = 0., True, {}
    for day_plan in plan["itinerary"]:
        day, activities = day_plan["day"], day_plan["activities"]
        require(bool(activities), "empty_day", f"第{day}天没有活动")
        hotels = [a for a in activities if a["type"] == "accommodation"]
        require(len(hotels) == (1 if day < days else 0), "hotel_nights", f"第{day}天住宿晚数不正确")
        if hotels:
            require(activities[-1] is hotels[-1] and clock(hotels[-1]["end_time"]) == 1440,
                    "hotel_position", "住宿应是当天末项并覆盖至午夜")
        previous_end = walking = transport = attraction_count = 0
        meal_counts = {kind: 0 for kind in MEALS}
        for a in activities:
            kind = a["type"]
            begin, finish = clock(a["start_time"]), clock(a["end_time"])
            valid_time = begin is not None and finish is not None and 0 <= begin < finish <= 1440
            require(valid_time, "same_day_time", "当前入口要求每个活动和班次在标注日期内有正时长")
            cursor = previous_end
            for leg in a["transports"]:
                start, end = clock(leg["start_time"]), clock(leg["end_time"])
                valid_leg = start is not None and end is not None and cursor <= start <= end <= 1440
                require(valid_leg, "transport_timeline", "接驳须在前项结束后依次发生，不能倒流或重叠")
                if valid_leg:
                    transport += end - start
                    walking += end - start if leg["mode"] == "walk" else 0
                    cursor = end
                require(leg["mode"] in request.get("local_modes", ("metro", "taxi", "walk")),
                        "local_mode", "市内交通方式不符合要求")
                factor = 0 if leg["mode"] == "walk" else people if leg["mode"] == "metro" else math.ceil(people / 4)
                require(_same_price(leg.get("cost"), leg.get("price", -1) * factor),
                        "transport_cost", "接驳费用与人数、车数不一致")
                if leg["mode"] == "metro":
                    require(leg.get("tickets") == people, "transport_tickets", "地铁票数与人数不一致")
                elif leg["mode"] == "taxi":
                    require(leg.get("cars") == math.ceil(people / 4), "transport_cars", "出租车数量与人数不一致")
            if valid_time:
                require(begin >= cursor, "activity_timeline", "活动须在前项和接驳结束后开始")
                if len(tickets) == 2 and a is tickets[-1][1]:
                    require(begin - cursor >= (60 if kind == "train" else 120),
                            "departure_buffer", "返程进站或登机缓冲不足")
                previous_end = finish
            for item in (a, *a["transports"]):
                for field in ("price", "cost"):
                    require(_number(item.get(field)), "cost", "费用必须有可核验的非负数值")
                if _number(item.get("cost")):
                    total += item["cost"]
                else:
                    cost_complete = False
            if kind in INTERCITY | {"attraction"}:
                require(a.get("tickets") == people, "tickets", "票数与人数不一致")
                require(_same_price(a.get("cost"), a["price"] * people), "activity_cost", "票价乘人数与总费用不一致")
            elif kind in MEALS:
                require(_same_price(a.get("cost"), a["price"] * people), "meal_cost", "餐费乘人数与总费用不一致")
                meal_counts[kind] += 1
                if kind in MEAL_WINDOWS and valid_time:
                    opens, latest, closes = MEAL_WINDOWS[kind]
                    require(opens <= begin < latest and finish <= closes and finish - begin >= 60,
                            "meal_window", "午晚餐须在餐窗内保留一小时")
            if kind == "accommodation":
                rooms = a.get("rooms", 0)
                require(rooms > 0, "room_count", "住宿需要安排正数间房")
                require(request.get("rooms") is None or rooms == request["rooms"],
                        "rooms", "房间数量与需求不一致")
                if not request.get("reference_lodging"):
                    beds = a.get("room_type", 0)
                    per_room = request.get("snapshot_room_occupancy", beds)
                    require(rooms > 0 and beds > 0 and rooms * per_room >= people, "capacity", "房间容量不足")
                    require(request.get("room_type") is None or beds == request["room_type"],
                            "room_type", "房间配置与需求不一致")
                require(_same_price(a.get("cost"), a["price"] * rooms), "hotel_cost", "房费乘房间数与总费用不一致")
                cap = request.get("hotel_price_limit")
                require(cap is None or a["price"] <= cap, "hotel_price", "房费超过每晚每间上限")
                party_cap = request.get("hotel_party_price_limit")
                require(party_cap is None or a["price"] * rooms <= party_cap + 1e-6,
                        "hotel_party_price", "全部房间的每晚总价超过住宿上限")
                required_hotels = set(request.get("required_hotel_names", ()))
                require(not required_hotels or required_hotels == {a.get("position")},
                        "hotel_name", "住宿不是指定的酒店")
            elif kind in MEALS:
                cap = request.get("food_price_limit")
                require(cap is None or a["price"] <= cap, "food_price", "餐费超过每人每餐上限")
            if kind in INTERCITY:
                require(kind in request.get("intercity_modes", ("train", "airplane")),
                        "intercity_mode", "城际交通方式不符合要求")
                reverse = len(tickets) == 2 and a is tickets[-1][1]
                origin, target = (city, request.get("start_city")) if reverse else (request.get("start_city"), city)
                key = (origin, target, kind)
                try:
                    if key not in source_tickets:
                        frame = env.intercitytransport.select(origin, target, kind)
                        source_tickets[key] = [] if frame is None else frame.to_dict("records")
                    id_field = "TrainID" if kind == "train" else "FlightID"
                    matching = [r for r in source_tickets[key] if str(r.get(id_field)) == str(a.get(id_field))
                                and r.get("From") == a["start"] and r.get("To") == a["end"]
                                and r.get("BeginTime") == a["start_time"] and r.get("EndTime") == a["end_time"]
                                and _same_price(a["price"], r.get("Cost"))]
                    require(bool(matching), "ticket_evidence", "班次、站点、时刻或票价与当前环境不一致")
                    direction = "return" if reverse else "outbound"
                    restrictions = request.get("ticket_restrictions", {}).get(direction, ())
                    if restrictions:
                        require(any(all(ticket_matches(kind, row, options) for options in restrictions)
                                    for row in matching), "intercity_service",
                                f"所选{('返程' if reverse else '去程')}班次类型不满足要求")
                except (AttributeError, KeyError, TypeError, ValueError) as error:
                    require(False, "ticket_evidence_unavailable", str(error))
                continue
            table = "attractions" if kind == "attraction" else "accommodations" if kind == "accommodation" else "restaurants"
            rows = [r for r in catalogs[table] if r.get("name") == a.get("position")]
            hotel_breakfast = (kind == "breakfast" and not rows and a["price"] == 0 and
                               any(r.get("name") == a.get("position") for r in catalogs["accommodations"]))
            priced = [r for r in rows if _same_price(a["price"], r.get("price"))]
            require(bool(priced) or hotel_breakfast, "poi_evidence", "地点或单位价格与当前环境不一致: " + a.get("position", ""))
            if kind == "accommodation":
                room_rows = priced
                if not request.get("reference_lodging"):
                    room_rows = [r for r in priced if _same_price(a.get("room_type"), r.get("numbed"))]
                    require(bool(room_rows), "hotel_room_evidence", "房型与当前环境不一致")
                features = request.get("required_hotel_features", ())
                if features:
                    require(any(required_features_present(r.get("featurehoteltype"), features,
                                                          request.get("hotel_feature_options")) for r in room_rows),
                            "hotel_feature", f"{a.get('position', '')}缺少指定设施证据")
            elif not hotel_breakfast:
                for row in priced:
                    opens, closes = clock(row.get("opentime")), clock(row.get("endtime"))
                    if opens is not None and closes is not None:
                        closes += 1440 if closes <= opens else 0
                        if valid_time and opens <= begin < finish <= closes:
                            break
                else:
                    require(False, "opening_hours", "活动不在可核验营业时间内: " + a.get("position", ""))
                if kind == "attraction":
                    attraction_count += 1
                    visited["attractions"].add(a["position"])
                    categories["attraction_types"].update(str(r["type"]) for r in priced if r.get("type"))
                else:
                    visited["restaurants"].add(a["position"])
                    categories["restaurant_types"].update(str(r["cuisine"]) for r in priced if r.get("cuisine"))
        for kind, count in meal_counts.items():
            require(count <= 1, "repeated_meal", f"第{day}天重复安排{kind}")
        if len(tickets) == 2:
            arrival = clock(tickets[0][1]["end_time"]) if day == 1 else 0
            terminal = clock(tickets[1][1]["start_time"]) if day == days else 1440
            if day == days and terminal is not None:
                terminal -= 60 if tickets[1][1]["type"] == "train" else 120
            if arrival is not None and terminal is not None:
                for kind, (opens, latest, closes) in MEAL_WINDOWS.items():
                    earliest = max(arrival, opens)
                    applicable = earliest < latest and earliest + 60 <= min(terminal, closes)
                    require(not applicable or meal_counts[kind] == 1, "missing_meal", f"第{day}天在目的地有完整餐窗但缺少{kind}")
        for key, actual in (("max_daily_walk_minutes", walking), ("max_daily_transport_minutes", transport),
                            ("max_daily_attractions", attraction_count)):
            limit = request.get("profile", {}).get("hard_limits", {}).get(key)
            require(limit is None or actual <= limit, "personalization_limit", f"第{day}天超过{key}")

    require(bool(visited["attractions"]), "no_visits", "行程至少需要一处游览")
    for field, observed in {**visited, **categories}.items():
        for value in request.get("must_" + field, ()):
            require(value in observed, "required_" + field, "缺少必需项目 " + value)
        for value in request.get("excluded_" + field, ()):
            require(value not in observed, "excluded_" + field, "出现已排除项目 " + value)
    cap = request.get("budget")
    require(cap is None or total <= cap + .01, "budget", "整趟费用超过总预算")
    if len(tickets) == 2:
        go, back = tickets[0][1], tickets[-1][1]
        stations = request.get("outbound_arrival_stations", ())
        require(all(go["end"] == station for station in stations),
                "outbound_arrival_station", "去程抵达站点不符合要求")
        period = request.get("outbound_arrival_period")
        if period:
            observed = clock(go["end_time"])
            require(observed is not None and clock(period[0]) <= observed < clock(period[1]),
                    "outbound_arrival_period", "去程抵达时段不符合要求")
        for field, actual, relation in (("outbound_earliest", go["start_time"], "min"),
                                        ("outbound_latest", go["start_time"], "max"),
                                        ("return_departure_latest", back["start_time"], "max"),
                                        ("return_arrival_latest", back["end_time"], "max")):
            limit = request.get(field)
            if limit:
                observed, bound = clock(actual), clock(limit)
                require(observed is not None and bound is not None and
                        (observed >= bound if relation == "min" else observed <= bound),
                        field, "城际班次时刻不符合要求")
    # Known failures need no further expensive route queries. Native grounding
    # still gates every candidate that could otherwise be declared successful.
    if not issues:
        issues.extend(_commonsense_issues(request, plan))
    return {"passed": not issues, "issues": issues,
            "total_cost": round(total, 2) if cost_complete else None,
            "protocol": "typed_request_v2", "scope": "same_day_intercity_tickets"}
