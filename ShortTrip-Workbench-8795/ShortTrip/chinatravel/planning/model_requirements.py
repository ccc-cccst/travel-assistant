"""DeepSeek interpretation for the public planner. All constraints are grounded locally."""

import json
import os
import re
import unicodedata

from chinatravel.agent.llms import create_llm
from chinatravel.agent.nesy_agent.constraint_compiler import _validate_extraction_items
from chinatravel.agent.nesy_agent.extraction_alignment import aliases, source_modality
from chinatravel.agent.nesy_agent.requirement_inventory import metadata_only_source

from .requirements import NUMBER, _assign, _catalog, _clock, _issue, _number, split_clauses


MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
PRICE_FIELDS = {"budget": "cost", "hotel_price_limit": "hotel_price", "food_price_limit": "food_price"}
PREFERENCE_CUES = {
    "walking": r"少走|少步行|多走|步行|走路",
    "pace": r"轻松|不赶|慢慢|宽松|节奏",
    "transport": r"少换乘|少通勤|少折返|少坐车|少绕路",
    "experience": r"多玩|多逛|多游览|玩够",
    "first_day_sightseeing": r"第一天|首日",
}
INSTRUCTION = """你负责抽取中国短途出行需求，只输出 JSON 对象 {"requirements": [...]}。
检查用户需求的每个分句。source 用原文的完整分句，去掉首尾标点，不要改写或省略限定词。
每项包含 kind、source、field、value，同一个分句可有多项。
hard 用已经在输入中出现的硬约束，field 使用现有结构化字段名，value 是原文支持的值。
全程费用用 budget，房费和餐费用 hotel_price_limit 和 food_price_limit，金额用数字。
火车往返使用 intercity_modes=["train"]，明确房间数用 rooms，截止时间用 HH:MM。
每日步行上限用 max_daily_walk_minutes，市内交通上限用 max_daily_transport_minutes，
每日景点上限用 max_daily_attractions。时间长度单位为分钟。
preference 用于喜欢、希望、尽量等软要求。按 walking、pace、transport、experience、
first_day_sightseeing 或已有 preferred_ 字段标记。没有可表达字段时可用 field="other"。
已经预约用 appointment，value 为 {"day":2,"place":"原文地点","start_time":"15:00","end_time":"16:00"}。
未说明的预约字段为 null。地点有歧义时保留原文，不猜馆区，不确认库存。
不能映射的硬要求或条件用 pending。纯出行人数、城市、天数等已理解的基本背景用 context。
保留互相牵制的要求。不得自行修改已填写的值，不要生成行程、报价或 DSL 代码。"""


def configured_model():
    """Keep credentials server-side and pin the Flash integration to DeepSeek."""
    name = os.getenv("CHINATRAVEL_OPENAI_MODEL", "").strip()
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if name != MODEL and not (not name and deepseek_key):
        return None
    key = os.getenv("CHINATRAVEL_OPENAI_API_KEY") or deepseek_key
    url = os.getenv("CHINATRAVEL_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or BASE_URL
    if not key or url.rstrip("/") not in {BASE_URL, BASE_URL + "/v1"}:
        raise ValueError("DeepSeek Flash 需要后端环境变量中的有效密钥和官方地址")
    return create_llm(MODEL, base_url=url, api_key=key, wire_api="chat",
                      token_limit_arg="max_tokens", max_tokens=1800, max_retries=0)


def _normalized(text):
    return unicodedata.normalize("NFKC", text)


def _known_source(request, source):
    normalized = _normalized(source)
    if metadata_only_source(vars(request), normalized):
        return True
    if re.fullmatch(r"(?:(?:请|帮|麻烦)(?:你|我)?)+\s*(?:(?:规划|安排)(?:一下|个行程|行程)?)?", normalized):
        return True
    if re.fullmatch(rf"(?:住|开|订|要)?{NUMBER}\s*间(?:房|客房)?", normalized):
        return request.rooms is not None
    return any(_normalized(item["source"]) == normalized and item["source"] != item["field"]
               for item in request.evidence)


def _hard(request, field, value, source):
    if not isinstance(field, str):
        return False
    field = field.removeprefix("profile.hard_limits.").removeprefix("hard_limits.")
    if any(item["field"] == field and item["value"] == value
           and _normalized(item["source"]) == _normalized(source) for item in request.evidence):
        return True
    if field in PRICE_FIELDS:
        symbolic = PRICE_FIELDS[field]
        try:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Expected an amount")
            modality, _ = source_modality(request.nature_language, source, field=symbolic)
            if modality != "hard" or not re.search(r"控制|预算|上限|以内|最多|超|以下|不高于", source):
                raise ValueError("Expected a hard upper bound")
            if re.search(r"人均|每人", source) and field == "budget":
                raise ValueError("Use the already normalized group total")
            constraint = f"{symbolic} <= {value!r}"
            _validate_extraction_items(vars(request), {
                "hard_logic": [constraint],
                "hard_logic_evidence": [{"constraint": constraint, "source": source}],
            })
        except (TypeError, ValueError):
            if not _known_source(request, source):
                return False
        else:
            _assign(request, field, value, source)
            return True
    old = getattr(request, field, None) if isinstance(field, str) else None
    if isinstance(old, tuple) and isinstance(value, list):
        value = tuple(value)
    return old is not None and old == value and _known_source(request, source)


def _preference(request, field, source):
    if source_modality(request.nature_language, source)[0] not in {"soft", "negative_soft"}:
        return _known_source(request, source)
    request.soft_preferences = tuple(dict.fromkeys((*request.soft_preferences, source)))
    if field in PREFERENCE_CUES and re.search(PREFERENCE_CUES[field], source):
        if field in {"walking", "pace", "transport", "experience"}:
            request.preference_dimensions = tuple(dict.fromkeys((*request.preference_dimensions, field)))
            request.evidence.append(dict(field=field, value=True, source=source))
            return True
    if not _known_source(request, source):
        _issue(request, "unscored_model_preference", source, "已保留这条偏好，当前没有对应的行程评分项", False)
    return True


def _appointment(request, env, value, source):
    if any(item["source"] == source for item in request.appointments):
        return True
    if not re.search(r"预约|订好|已订|预订了", source) or not isinstance(value, dict):
        return False
    clause = _normalized(source)
    try:
        day = value.get("day")
        day_text = re.search(rf"第\s*({NUMBER})\s*天", clause)
        if isinstance(day, bool) or not isinstance(day, int) or not day_text or day != _number(day_text[1]):
            day = None
        times = [_clock(t) for t in re.findall(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)", clause)]
        start = _clock(value["start_time"]) if value.get("start_time") else None
        end = _clock(value["end_time"]) if value.get("end_time") else None
        start = start if times and start == times[0] else None
        end = end if len(times) > 1 and end == times[1] and start and end > start else None
    except (TypeError, ValueError):
        return False
    mapping = aliases(_catalog(env, request.target_city, "attractions"))
    matches = [alias for alias in mapping if len(alias) > 1 and alias in clause]
    names = sorted({name for alias in matches if not any(alias != other and alias in other for other in matches)
                    for name in mapping[alias]})
    place = value.get("place")
    item = dict(day=day, place=place if isinstance(place, str) and place in source else None,
                place_options=names, start_time=start, end_time=end, source=source, confirmed=False)
    if item not in request.appointments:
        request.appointments.append(item)
    _issue(request, "appointment_requires_confirmation", source,
           "请核对预约凭证上的馆区和时段，再锁定这项预约。")
    return True


def interpret(request, env, model, timeout):
    clauses = split_clauses(request.nature_language)
    context = {key: getattr(request, key) for key in ("start_city", "target_city", "days", "people_number")}
    context["known_fields"] = sorted({item["field"] for item in request.evidence})
    context["原文分句"] = clauses
    message = model.chat([
        {"role": "system", "content": INSTRUCTION},
        {"role": "user", "content": json.dumps({"现有字段": context, "用户原话": request.nature_language},
                                                ensure_ascii=False)},
    ], response_format={"type": "json_object"}, temperature=0, timeout=timeout,
       extra_body={"thinking": {"type": "disabled"}})
    payload = json.loads(message["content"])
    if not isinstance(payload, dict) or not isinstance(payload.get("requirements"), list):
        raise ValueError("Invalid requirement JSON")
    covered = set()
    for item in payload["requirements"]:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            raise ValueError("Invalid requirement entry")
        source = item["source"].strip(" 。，,；;！？!?\n")
        if source not in clauses:
            continue
        kind, field, value = item.get("kind"), item.get("field"), item.get("value")
        if kind == "hard":
            accepted = _hard(request, field, value, source)
        elif kind == "preference":
            accepted = _preference(request, field if isinstance(field, str) else None, source)
        elif kind == "appointment":
            accepted = _appointment(request, env, value, source)
        elif kind == "context":
            accepted = _known_source(request, source)
        else:
            accepted = False
        if accepted:
            covered.add(source)
        elif kind != "context":
            _issue(request, "unmapped_model_requirement", source, "这条要求还没有映射到可核验的条件，请确认")
    for clause in clauses:
        if clause not in covered and not _known_source(request, clause) and not any(
                item["blocking"] and _normalized(item["source"]) in _normalized(clause)
                for item in request.issues if item["source"]):
            _issue(request, "unreviewed_requirement", clause, "这段原话尚未核实，请确认它是否是必须满足的条件")

