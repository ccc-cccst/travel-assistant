"""Translate one request about a published itinerary into an edit or a question."""

import re

from .tripspec import request_deepseek_json
from chinatravel.agent.nesy_agent.semantic_grounding import LOCAL_CUISINE


PLACE_KINDS = {"attraction": "attraction", "accommodation": "accommodation",
               "breakfast": "restaurant", "lunch": "restaurant", "dinner": "restaurant"}
CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00")
DAY = re.compile(r"第\s*(\d{1,2}|十[一二三四五六七八九]?|[一二三四五六七八九])\s*[天日]")
CHINESE_NUMBERS = "零一二三四五六七八九十"
CLAUSE = re.compile(r"[，,；;。！？]|顺便|另外|同时")
PACE_WORDS = re.compile(r"轻松|放松|宽松|松一点|别太赶|不要太赶|慢一点|缓一点|留.*空")
PERIODS = {"早上": "morning", "上午": "morning", "下午": "afternoon", "晚上": "evening"}
MEAL_NAMES = {"早餐": "breakfast", "早饭": "breakfast", "午餐": "lunch", "午饭": "lunch",
              "晚餐": "dinner", "晚饭": "dinner"}
TIME_RANGE = re.compile(r"(" + CLOCK.pattern + r")\s*(?:到|至|–|—|-)\s*(" + CLOCK.pattern + r")")
TIME_TARGET = re.compile(r"改[到成为]|调整[到为至]|移[到至]|提前[到至]|推迟[到至]")
REMOVE_WORDS = r"(?:取消|删掉|删除|移除|跳过|去掉)"
NEGATIVE_EDIT = re.compile(
    r"(?:不要|不想|不能|不许|别|不)\s*(?:把[^，,；;。]{1,35}|将[^，,；;。]{1,35})?"
    r"(?:换|改|动|" + REMOVE_WORDS + r")|保留|保持|照旧|不改|不变")
MEAL_CUISINES = re.compile(r"上海本地菜|上海菜|本帮菜|北京菜|湖北菜|江浙菜|客家菜|东北菜|川菜|粤菜|湘菜|苏菜|徽菜|鲁菜")
PROMPT = """把当前旅行者的原话转换成 JSON。仅输出 JSON 对象。
输入包含当前查看的日期、活动和已核验行程。活动 ID 必须来自输入。
动作取 batch、replace、meal_options、time、transport、optimize、hotel、explain、clarify 中的一项。
点名换店、酒店或景点用 replace，带 activity 和 place，place 抄自原话。
未点名换餐用 meal_options，带 activity。明确说了菜系才带 cuisine，菜系必须抄自原话。
time 带 activity、start 和 end，时间用 HH:MM，可从原时段和用户给的增量算新时刻。
transport 带 activity 和 mode（只能 walk、taxi、metro），activity 是接驳的目的地活动。
optimize 带 day，hotel 仅用于寻找接驳更方便的酒店。
只问当前费用、安排、预约或赶得上吗，返回 explain，可带 day。询问具体改动之后是否赶得上，返回对应试排操作。
用户明确指定的日期优先，否则可用当前日，或用用户指认且唯一的现有活动。
同一天有多项关联改动时用 {"action":"batch","day":2,"operations":[...],"pace":"lighter"}。
operations 内每项带 action 和对应活动 ID，支持 replace、meal_options、time、transport、remove_visit。
附近的饭店未点名时用 meal_options，可带原话指定的菜系。time 使用明确时刻或原话的增量，不能自定时刻。
只有用户明确指名允许删除的景点才使用 remove_visit。保留预约和原返程已经是默认约束。
仅在原话表达放松节奏时带 pace:lighter。可与一项明确动作组合。逐项保留原话中明确要求的调整和餐馆偏好。
若需用户决定删哪一站、具体哪家分店或节奏调整范围，用 clarify，带一句简短中文问题。不支持的动作也询问。
不可改预约和城际班次、不可自行挑分店、不可更改预算或捏造车次、价格和核验结果。"""


def _day_number(raw):
    if raw.isdecimal():
        return int(raw)
    if len(raw) == 2:
        return 10 + CHINESE_NUMBERS.index(raw[1])
    return CHINESE_NUMBERS.index(raw)


def _clarify(message):
    return {"state": "clarify", "message": message}


def _select(operation, message):
    return {"state": "action", "message": message, "operations": [operation]}


def _grounded_activities(clause, activities, day):
    """Only bind activities that can be identified from this clause on the selected day."""
    if day is None:
        return []
    meals = {kind for name, kind in MEAL_NAMES.items() if name in clause}
    matches = [item for item in activities if item["day"] == day and
               (item["name"] in clause or item["kind"] in meals)]
    return matches


def _pace_scope(clauses):
    for clause in clauses:
        if PACE_WORDS.search(clause) and not re.search(r"不需要|不用|无需|不想", clause):
            return True, next((period for word, period in PERIODS.items() if word in clause), None)
    return False, None


def _requested_time_range(clause, item):
    ranges = list(TIME_RANGE.finditer(clause))
    targets = list(TIME_TARGET.finditer(clause))
    if targets:
        # In "从原时段改为新时段", only the range after 改为 states the target.
        ranges = [span for span in ranges if span.start() >= targets[-1].end()]
        return ranges[0] if len(ranges) == 1 else None
    if len(ranges) == 1 and item["name"] in clause:
        span, = ranges
        prefix = clause[:span.start()]
        if not re.search(r"(?:从|由|原先|原来|原定|原本|目前)\s*(?:的|是|为)?\s*$", prefix):
            return span
    return None


def _verify_explicit_actions(clauses, operations, activities, day):
    for clause in clauses:
        matches = _grounded_activities(clause, activities, day)
        if len(matches) != 1:
            continue
        item, = matches
        actions = {op["action"]: op for op in operations if op.get("activity") == item["id"]}
        prohibited = NEGATIVE_EDIT.search(clause)
        if prohibited and actions:
            # "保留预约" refers to the booking itself; an incoming leg can still change.
            if any(action != "transport" for action in actions):
                return _clarify(f"「{item['name']}」在原话中要求保留。请确认要修改的是哪一项。")
            continue
        requires = None
        if item["kind"] in ("breakfast", "lunch", "dinner") and re.search(r"换|另找|找一家", clause):
            requires = {"replace", "meal_options"}
            cuisines = [*MEAL_CUISINES.findall(clause), *LOCAL_CUISINE.findall(clause)]
            meal = actions.get("meal_options")
            if meal is not None and cuisines and meal.get("cuisine") not in cuisines:
                return _clarify(f"请确认「{item['name']}」要找的菜系：{'、'.join(dict.fromkeys(cuisines))}。")
        elif explicit := _requested_time_range(clause, item):
            requires = {"time"}
            if "time" in actions and (actions["time"].get("start"), actions["time"].get("end")) != explicit.groups():
                return _clarify(f"「{item['name']}」的时间请按原话使用 {explicit[1]}–{explicit[2]}。")
        elif item["kind"] == "attraction" and re.search(REMOVE_WORDS, clause):
            requires = {"remove_visit"}
        if requires and requires.isdisjoint(actions):
            return _clarify(f"还有一项要求需要确认：「{clause.strip()}」。请确认后一起试排。")
    return None


def interpret_edit(payload, facts, provider=request_deepseek_json):
    text, activities = payload.get("text"), payload.get("activities")
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 600:
        raise ValueError("请输入修改要求或问题，最多 600 字。")
    if not isinstance(activities, list) or not activities or len(activities) > 240:
        raise ValueError("请选择有活动的已核验行程。")
    if payload["snapshot"] != facts.version:
        return _clarify("该行程的旅行数据版本不同，请重新打开可核验方案。")

    explicit_days = {_day_number(raw) for raw in DAY.findall(text)}
    if len(explicit_days) > 1:
        return _clarify("建议逐天处理。请先说要修改哪一天和哪一项活动。")
    preferred = next(iter(explicit_days), payload.get("day"))
    days = {item["day"] for item in activities}
    if explicit_days and preferred not in days:
        return _clarify(f"当前方案没有第 {preferred} 天。请选择行程内的日期。")
    reply = provider(PROMPT, {"text": text, "day": preferred, "focus": payload.get("focus"),
                              "activities": [{k: item[k] for k in ("id", "day", "kind", "name", "start", "end", "locked")}
                                             for item in activities]}, max_tokens=750, timeout=14)
    if reply.error == "missing_model_credentials":
        return {"state": "unavailable", "message": "行程对话尚未配置 DeepSeek。可以从时间线或地图继续手动修改。"}
    if reply.error or not isinstance(reply.draft, dict):
        return {"state": "unavailable", "message": "这次没有成功理解请求。可以重试，或从时间线手动修改。"}
    draft = reply.draft
    action = draft.get("action")
    if action == "clarify":
        question = draft.get("question")
        return _clarify(question if isinstance(question, str) and 0 < len(question.strip()) <= 100
                        else "请说明要修改的日期、活动和期望的调整。")
    day = draft.get("day", preferred)
    if type(day) is not int or day not in days:
        day = preferred if preferred in days else None
    if explicit_days:
        day = preferred
    clauses = CLAUSE.split(text)
    lighter, period = _pace_scope(clauses)
    if action == "explain":
        metrics = payload["metrics"]
        facts_text = [f"这份方案的整团参考费用为 ¥{metrics['total_cost_cny']:.2f}，"
                      f"市内接驳共 {metrics['city_transit_minutes']} 分钟。"]
        for item in activities:
            if item["kind"] not in ("train", "airplane") and not item["locked"]:
                continue
            if day is not None and day != item["day"]:
                continue
            detail = item["arrival_note"]
            facts_text.append(f"第 {item['day']} 天 {item['start']} · {item['name']}。{detail}")
        return {"state": "answer", "message": "\n".join(facts_text)}
    if action == "hotel":
        return _select({"action": "hotel"}, "可以试排接驳更短的酒店并复核各晚住宿。")
    if action == "optimize":
        if day is None:
            return _clarify("请问要顺路优化第几天？")
        return _select({"action": "optimize", "day": day}, f"将试排第 {day} 天的游览顺序。")

    if action == "batch" or (lighter and action in ("time", "replace", "transport", "meal_options")):
        operations = draft.get("operations") if action == "batch" else [draft]
        return _batch(operations, payload, text, clauses, activities, day, facts, lighter, period)
    if issue := _verify_explicit_actions(clauses, [draft], activities, day):
        return issue
    return _activity_action(draft, payload, text, activities, preferred if explicit_days else None, facts)


def _batch(operations, payload, text, clauses, activities, day, facts, lighter, period):
    if day is None or not isinstance(operations, list) or not (1 if lighter else 2) <= len(operations) <= 3:
        return _clarify("请说明要修改哪一天，以及希望一起调整的安排。")
    resolved, checklist = [], []
    for instruction in operations:
        if not isinstance(instruction, dict) or instruction.get("action") not in (
                "time", "replace", "transport", "meal_options", "remove_visit"):
            return _clarify("这组修改包含暂不支持的操作。请指定调整的时段、景点、餐馆或接驳。")
        result = _activity_action(instruction, payload, text, activities, day, facts, combined=True)
        if result["state"] != "action":
            return result
        if len(result["operations"]) != 1:
            names = "、".join(op["place"] for op in result["operations"])
            return _clarify(f"请在原修改要求中补充具体分店：{names}。")
        operation, = result["operations"]
        if any(op["activity"] == operation["activity"] and op["action"] == operation["action"] for op in resolved):
            return _clarify("同一活动收到重复修改，请确认最终要求。")
        if operation["action"] == "meal_options" and any(op["action"] == "meal_options" for op in resolved):
            return _clarify("请指定其中一家餐馆名，另一家可以按前后两站试找。")
        resolved.append(operation)
        checklist.append(result["message"])
    if issue := _verify_explicit_actions(clauses, resolved, activities, day):
        return issue
    if lighter:
        when = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}.get(period, "当天")
        checklist.append(f"在{when}减少接驳或游览占用。")
    return {"state": "proposal", "message": f"我会一起试排第 {day} 天的这些调整。",
            "proposal": {"schema_version": 1, "day": day, "operations": resolved,
                         "pace": "lighter" if lighter else None, "pace_period": period, "checklist": checklist,
                         "locks": [{"name": item["name"], "day": item["day"], "start": item["start"]}
                                   for item in activities if item["locked"]]}}


def _activity_action(draft, payload, text, activities, required_day, facts, *, combined=False):
    action = draft.get("action")
    item = next((item for item in activities if item["id"] == draft.get("activity")), None)
    if item is None or (required_day is not None and item["day"] != required_day):
        return _clarify("请说明要修改哪一天的哪项活动，例如「第三天午餐」。")
    title = f"第 {item['day']} 天「{item['name']}」"
    kind = PLACE_KINDS.get(item["kind"])
    operation = {"action": action, "activity": item["id"]}
    if action == "transport":
        mode = draft.get("mode")
        if item["has_connection"] and mode in ("walk", "taxi", "metro"):
            label = {"walk": "步行", "taxi": "打车", "metro": "地铁"}[mode]
            return _select({**operation, "mode": mode}, f"试排去往{title}的{label}接驳。")
        return _clarify(f"请指定前往{title}的步行、打车或地铁接驳。")
    if item["locked"] or kind is None:
        return _clarify(f"{title}是固定事项。可以尝试调整它前后的活动或接驳。")
    if action == "remove_visit" and combined:
        if kind == "attraction" and item["name"] in text and re.search(r"取消|不去|删|移除|跳过|去掉", text):
            return _select(operation, f"试移除{title}。")
        return _clarify("请明确可以移除的景点名称，原预约和必去地点会保留。")
    if action == "replace" and kind == "accommodation" and combined:
        return _clarify("更换住宿会影响相邻日期，请通过酒店卡片单独试排。")
    if action == "time" and kind in ("attraction", "restaurant"):
        start, end = draft.get("start"), draft.get("end")
        if all(isinstance(t, str) and CLOCK.fullmatch(t) for t in (start, end)) and start < end:
            return _select({**operation, "start": start, "end": end}, f"将核验{title}改到 {start}–{end} 的影响。")
        return _clarify(f"请给出{title}的新起止时间，或说明要延长多久。")
    if action == "meal_options" and kind == "restaurant":
        cuisine = draft.get("cuisine")
        if cuisine is None or cuisine == "":
            return _select(operation, f"将按{title}前后两站查找可试排的餐馆。")
        if isinstance(cuisine, str) and 0 < len(cuisine.strip()) <= 24 and cuisine.strip() in text:
            return _select({**operation, "cuisine": cuisine.strip()},
                           f"将按{title}的前后接驳查找「{cuisine.strip()}」餐馆。")
        return _clarify("想试试哪种菜系？也可以直接说一家餐馆的名称。")
    if action == "replace":
        name = draft.get("place")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 120 or name.strip() not in text:
            return _clarify(f"你希望把{title}换成哪个地点？")
        match = facts.place_with_suggestions(payload["city"], name.strip(), kind)
        names = list(dict.fromkeys(record["name"] for record in match["records"]
                                  if record["planning_catalog"] and record["routable_in_snapshot"]
                                  and record["name"] != item["name"]))[:6]
        if not names:
            return _clarify(f"在当前城市的规划目录中查不到可核验的「{name.strip()}」。请补充全名或分店。")
        options = [{**operation, "place": resolved} for resolved in names]
        message = (f"将试排把{title}换为「{names[0]}」。" if match["status"] == "found"
                   else f"要替换的是{title}。请选择你指的地点或分店。")
        return {"state": "action", "message": message, "operations": options}
    return _clarify("请先指定一项操作，例如换一家饭店、调整时间或优化某一天。")
