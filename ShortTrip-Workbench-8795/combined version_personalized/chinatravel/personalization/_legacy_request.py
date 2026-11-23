"""Public request normalization with explicit evidence and unresolved requirements.

This adapter uses request text and public form fields. Benchmark DSL annotations
are deliberately never inspected. Unknown hard requirements remain blocking.
"""

from dataclasses import asdict, dataclass, field
import math
import re
import unicodedata

from chinatravel.agent.nesy_agent.constraint_compiler import chinese_number
from chinatravel.agent.nesy_agent.extraction_alignment import aliases
from chinatravel.agent.nesy_agent.semantic_grounding import TAG_ALIASES


NUMBER = r"(?:\d+(?:\.\d+)?[万千]?|[零一二两三四五六七八九十百千万]+)"
SOFT = re.compile(r"最好|尽量|尽可能|优先|如果|希望|喜欢|想|为主|主要")
STRONG = re.compile(r"必须|一定|务必|只能|非去不可|要安排|要去|排进|安排一餐")
CAP = re.compile(r"最多|不超过|不能超过|不得超过|别超过|上限|以内|预算|合计|总计|加起来|全程|整趟|一共")
NEGATIVE = re.compile(r"不想去|不要去|不去|别去|避免去|不吃|不要吃|不想吃")
BOOKED = re.compile(r"已(?:经)?预约|预约了|订好|已订|预订了")
VALUE_FIELDS = {"budget", "hotel_price_limit", "food_price_limit", "rooms", "room_type"}
TIME_FIELDS = {"outbound_earliest", "outbound_latest", "return_departure_latest", "return_arrival_latest"}
MODE_FIELDS = {"intercity_modes", "local_modes"}
PLACE_FIELDS = {prefix + kind for prefix in ("must_", "preferred_", "excluded_")
                for kind in ("attractions", "restaurants", "attraction_types", "restaurant_types")}
FORM_FIELDS = VALUE_FIELDS | TIME_FIELDS | MODE_FIELDS | PLACE_FIELDS
PUBLIC_FIELDS = FORM_FIELDS | {"uid", "start_city", "target_city", "days", "people_number",
                             "nature_language", "profile", "constraints", "appointment_choices"}
# Accepted metadata and evaluation labels have no influence on interpreted facts.
IGNORED_FIELDS = {"tag", "nature_language_en", "hard_logic", "hard_logic_py", "hard_logic_nl",
                  "soft_logic", "soft_logic_py", "soft_logic_nl", "soft_preferences",
                  "limit_rooms", "limits_room_type"}


@dataclass
class PlanningRequest:
    uid: str = ""
    nature_language: str = ""
    start_city: str = ""
    target_city: str = ""
    days: int = 0
    people_number: int = 1
    budget: float | None = None
    hotel_price_limit: float | None = None
    food_price_limit: float | None = None
    rooms: int | None = None
    room_type: int | None = None
    intercity_modes: tuple = ("train", "airplane")
    local_modes: tuple = ("metro", "taxi", "walk")
    outbound_earliest: str | None = None
    outbound_latest: str | None = None
    return_departure_latest: str | None = None
    return_arrival_latest: str | None = None
    must_attractions: tuple = ()
    must_restaurants: tuple = ()
    preferred_attractions: tuple = ()
    preferred_restaurants: tuple = ()
    excluded_attractions: tuple = ()
    excluded_restaurants: tuple = ()
    must_attraction_types: tuple = ()
    must_restaurant_types: tuple = ()
    excluded_attraction_types: tuple = ()
    excluded_restaurant_types: tuple = ()
    preferred_attraction_types: tuple = ()
    preferred_restaurant_types: tuple = ()
    soft_preferences: tuple = ()
    preference_dimensions: tuple = ()
    appointments: list = field(default_factory=list)
    profile: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)

    @property
    def ready(self):
        return not any(item["blocking"] for item in self.issues)

    def to_dict(self):
        return asdict(self)


def _issue(request, code, source, message, blocking=True):
    item = dict(code=code, source=source, message=message, blocking=blocking)
    if item not in request.issues:
        request.issues.append(item)


def _number(value):
    if isinstance(value, bool):
        raise ValueError("布尔值不是数量")
    text = str(value).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([万千]?)", text)
    result = float(match[1]) * {"": 1, "千": 1000, "万": 10000}[match[2]] if match else float(chinese_number(text))
    if not math.isfinite(result) or result < 0:
        raise ValueError("数量应为非负有限数")
    return result


def _assign(request, name, value, source, *, integer=False):
    try:
        value = _number(value)
        if integer and (value < 1 or not value.is_integer()):
            raise ValueError("人数、天数和房间数应为正整数")
        value = int(value) if integer else value
    except (ValueError, TypeError, KeyError, OverflowError):
        _issue(request, "invalid_value", source, f"{name} 的数值无效")
        return
    previous = [e for e in request.evidence if e["field"] == name]
    if previous and getattr(request, name) != value:
        _issue(request, "conflicting_values", source, f"{name} 存在相互冲突的值，请明确采用哪个")
        return
    setattr(request, name, value)
    request.evidence.append(dict(field=name, value=value, source=source))


def _clock(value):
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value))
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError("时刻应为00:00至23:59")
    return f"{int(match[1]):02d}:{int(match[2]):02d}"


def _set_clock(request, name, value, source):
    try:
        value = _clock(value)
    except ValueError:
        _issue(request, "invalid_time", source, f"{name} 的时刻无效")
        return
    old = getattr(request, name)
    if old and old != value:
        _issue(request, "conflicting_times", source, f"{name} 存在冲突的时刻")
        return
    setattr(request, name, value)
    request.evidence.append(dict(field=name, value=value, source=source))


def _catalog(env, city, kind):
    tool = getattr(env, kind, None)
    rows = getattr(tool, "data", {}).get(city)
    if rows is None or "name" not in rows:
        return set()
    return set(rows["name"].dropna().astype(str))


def _basic(request, query, text, cities):
    for name in ("start_city", "target_city"):
        value = query.get(name)
        if isinstance(value, str):
            setattr(request, name, value.strip())
    city_pattern = "|".join(re.escape(c) for c in sorted(cities, key=len, reverse=True))
    if city_pattern:
        start = re.search(rf"(?:从|当前位置|人在|现在在|在)\s*({city_pattern})", text)
        target = re.search(rf"(?:去(?:一趟)?|前往|目的地|目标位置|往返)\s*({city_pattern})", text)
        # City prefixes inside venue names (e.g. 南京路步行街) are not
        # contradictory destination statements. Keep existing inference, but
        # compare explicit form values only against clear city references.
        city_end = r"(?=$|[^\u4e00-\u9fff]|去|玩|旅行|旅游|游玩|坐|待|住|出发|过|想去|准备去|打算去|[一二两三四五六七八九十]+\s*(?:天|日))"
        explicit_start = re.search(rf"从\s*({city_pattern})(?:市)?{city_end}", text)
        explicit_target = re.search(rf"(?:去(?:一趟)?|前往|目的地|目标位置)\s*({city_pattern})(?:市)?{city_end}", text)
        paired = re.search(rf"({city_pattern})(?:市)?\s*往返\s*({city_pattern})(?:市)?{city_end}", text)
        if paired:
            for name, city in zip(("start_city", "target_city"), paired.groups()):
                if getattr(request, name) and getattr(request, name) != city:
                    _issue(request, "conflicting_values", paired[0], "表单城市与往返描述不一致")
                else:
                    setattr(request, name, city)
        for name, match in (("start_city", start), ("target_city", target)):
            if match and not getattr(request, name):
                setattr(request, name, match[1])
        for name, match in (("start_city", explicit_start), ("target_city", explicit_target)):
            if match and query.get(name) and getattr(request, name) != match[1]:
                _issue(request, "conflicting_values", match[0],
                       f"{name} 的表单值与文字描述不一致，请明确采用哪个")
    for name in ("start_city", "target_city"):
        value = getattr(request, name)
        if not value:
            _issue(request, "missing_field", name, "请提供出发城市和目的城市")
        elif cities and value not in cities:
            _issue(request, "unsupported_city", value, "该城市尚未安装可规划的数据")
    for name in ("days", "people_number"):
        if query.get(name) is not None:
            _assign(request, name, query[name], name, integer=True)
    trip_duration_text = re.sub(rf"第\s*{NUMBER}\s*天", "", text)
    days = re.search(rf"({NUMBER})\s*(?:天|日游)", trip_duration_text)
    if days:
        _assign(request, "days", days[1], days[0], integer=True)
    elif not request.days and re.search(r"当天|一日游", text):
        _assign(request, "days", 1, "当天", integer=True)
    if not request.days:
        _issue(request, "missing_field", "days", "请提供旅行天数")
    people = re.search(rf"(?:共|我们)?\s*({NUMBER})\s*个?人", text)
    friends = re.search(rf"我和({NUMBER})个朋友", text)
    if people:
        _assign(request, "people_number", people[1], people[0], integer=True)
    elif friends:
        _assign(request, "people_number", _number(friends[1]) + 1, friends[0], integer=True)
    elif not query.get("people_number"):
        if re.search(r"我俩|我们俩|我和(?:朋友|同学|对象|室友|妈妈|爸爸|同事|家人)|两人", text):
            _assign(request, "people_number", 2, "同行两人", integer=True)
        elif "我们仨" in text:
            _assign(request, "people_number", 3, "我们仨", integer=True)
        elif re.search(r"一个人|我一人|独自", text):
            _assign(request, "people_number", 1, "一个人", integer=True)
        else:
            request.assumptions.append("未说明人数，暂按1人规划，可在需求卡中调整")


def _amounts(request, clauses):
    for clause in clauses:
        if not CAP.search(clause):
            continue
        if re.search(rf"({NUMBER})\s*(?:到|至|[-~])\s*({NUMBER})", clause):
            _issue(request, "amount_range", clause, "预算区间请确认需要采用的上限，可在表单单独填写")
            continue
        amounts = list(re.finditer(rf"({NUMBER})\s*(?:元|人民币|块)", clause))
        if not amounts:
            amounts = list(re.finditer(rf"(?:预算|上限)\s*(?:为|是|不超过|最多)?\s*({NUMBER})(?!\d)", clause))
        if not amounts and re.search(r"(?:全程|整趟|总|合计)(?:花销|花费|费用|开销)", clause):
            amounts = list(re.finditer(rf"(?:控制在|不超过)\s*({NUMBER})\s*(?:以内|以下)?$", clause))
        for amount in amounts:
            prefix = clause[:amount.start()]
            recent = prefix[-35:]
            hotel = bool(re.search(r"房费|房价|每间每晚|住宿每晚|酒店每晚", recent))
            food = bool(re.search(r"每餐|每顿|每人每餐|人均每餐", recent))
            name = "hotel_price_limit" if hotel else "food_price_limit" if food else "budget"
            # A total explicitly covers transport, lodging and meals in one scope.
            if re.search(r"全程|整趟|总(?:费用|开销|预算|价|计)|合计|加起来|一共|全部行程", recent):
                name = "budget"
            if re.search(r"最好|尽量|尽可能", clause) and not STRONG.search(clause):
                request.soft_preferences += (clause,)
                _issue(request, "unscored_numeric_preference", clause,
                       "这项带金额的软偏好已保留，当前没有直接计入评分；可用明确限价字段设为硬限制", False)
                continue
            if name == "budget" and re.search(r"住宿|酒店|房费|餐饮|餐费|吃饭|饭钱|门票|车票", recent):
                total_scope = re.search(r"全程|整趟|合计|总(?:费用|开销|预算|价|计)|加起来|一共|全部行程", recent)
                # Several expenses listed with a total are a trip-wide scope.
                if not total_scope:
                    _issue(request, "ambiguous_amount_scope", clause,
                           "请确认这笔金额是全程总预算、每间每晚房费，还是每人每餐餐费")
                    continue
            value = _number(amount[1])
            if name == "budget" and re.search(r"人均|每人", recent):
                value *= request.people_number
            _assign(request, name, value, clause)


def _rooms_modes(request, text, clauses):
    for clause in clauses:
        if not re.search(r"最好|尽量|如果", clause):
            for match in re.finditer(rf"({NUMBER})\s*间(?:房|客房|酒店)?", clause):
                # "别把三个人往一间里塞" does not request one room.
                if re.search(r"别|不要|不能|各住|各开", clause[:match.start()]):
                    continue
                _assign(request, "rooms", match[1], match[0], integer=True)
            if "双床房" in clause:
                _assign(request, "room_type", 2, clause, integer=True)
            elif re.search(r"单床房|大床房", clause):
                _assign(request, "room_type", 1, clause, integer=True)
    positive, negative, mode_clauses = set(), set(), set()
    for mode, words in (("train", "火车|高铁|动车"), ("airplane", "飞机|航班")):
        for clause in clauses:
            if re.search(words, clause):
                if re.search(rf"不(?:想)?(?:坐|乘)|拒绝", clause):
                    negative.add(mode)
                    mode_clauses.add(clause)
                elif re.search(r"坐|乘|往返|来回|城际|火车\s*(?:去|玩|游|回来|返回)", clause):
                    if re.search(r"最好|尽量|都可以|均可", clause):
                        request.soft_preferences += (clause,)
                    else:
                        positive.add(mode)
                        mode_clauses.add(clause)
    allowed = (positive or set(request.intercity_modes)) - negative
    request.intercity_modes = tuple(m for m in ("train", "airplane") if m in allowed)
    request.evidence.extend(dict(field="intercity_modes", value=request.intercity_modes, source=clause)
                            for clause in sorted(mode_clauses))
    if not allowed:
        _issue(request, "conflicting_modes", text, "可用城际交通方式为空")
    if len(positive) > 1 and re.search(r"去程|回程|返程|回来|返回", text):
        _issue(request, "directional_modes", text, "请分别明确去程和返程交通方式，目前此入口使用统一城际方式")
    banned = set()
    for mode, words in (("taxi", "打车|出租车"), ("metro", "地铁"), ("walk", "步行|走路")):
        if re.search(rf"(?:不|禁止|避免)(?:要|能|想|坐|乘)?(?:{words})", text):
            banned.add(mode)
        if re.search(rf"只(?:能)?(?:坐|乘|用)?(?:{words})", text):
            request.local_modes = (mode,)
    request.local_modes = tuple(m for m in request.local_modes if m not in banned)
    if not request.local_modes:
        _issue(request, "conflicting_modes", text, "可用市内交通方式为空")


def _times(request, clauses):
    for clause in clauses:
        if not re.search(r"最晚|最迟|不晚于|不超过|之前|前|上限", clause):
            continue
        match = re.search(rf"(上午|早上|下午|晚上|晚间)?\s*({NUMBER})(?::(\d{{2}})|点(?:钟)?(?:(半)|({NUMBER})分?)?)", clause)
        if not match:
            continue
        hour = int(_number(match[2]))
        minute = int(match[3] or (30 if match[4] else _number(match[5]) if match[5] else 0))
        if match[1] in ("下午", "晚上", "晚间") and hour < 12:
            hour += 12
        if not re.search(r"返程|归程|回程|回来|回到|返回|回.+火车|上火车|上车|去程|出发|(?:发车|开车|起飞)\s*回", clause):
            _issue(request, "unsupported_time_requirement", clause, "该活动时间要求需要进一步确认")
            continue
        field = "return_departure_latest"
        if re.search(r"去程|出发", clause) and not re.search(r"返程|归程|回程|返回", clause):
            field = "outbound_latest"
        elif re.search(r"抵达|到达|回到|返回到", clause):
            field = "return_arrival_latest"
        _set_clock(request, field, f"{hour:02d}:{minute:02d}", clause)


def add_appointment(request, env, source):
    """Recognize a stated reservation, retaining exact source evidence."""
    if any(item["source"] == source for item in request.appointments):
        return True
    if not BOOKED.search(source):
        return False
    day = re.search(rf"第\s*({NUMBER})\s*天", source)
    time = re.search(r"(\d{1,2}:\d{2})\s*[-–—~至到]\s*(\d{1,2}:\d{2})", source)
    try:
        holds = _number(day[1]) if day else None
        start, end = (_clock(value) for value in time.groups()) if time else (None, None)
    except (ValueError, TypeError):
        return False
    mapping = aliases(_catalog(env, request.target_city, "attractions"))
    matches = [alias for alias in mapping if len(alias) >= 2 and alias in source]
    longest = [alias for alias in matches if not any(alias != other and alias in other for other in matches)]
    names = sorted({name for alias in longest for name in mapping[alias]})
    if not names or len(longest) != 1 or not holds or holds % 1 or not start or not end or start >= end:
        return False
    request.appointments.append(dict(day=int(holds), place=longest[0], place_options=names,
                                     start_time=start, end_time=end, source=source, confirmed=False))
    _issue(request, "appointment_requires_confirmation", source, "请核对预约凭证上的馆区和时段，再锁定这项预约。")
    return True


def confirm_appointments(request, choices):
    if choices is None:
        return
    if not isinstance(choices, dict) or any(not isinstance(place, str) for place in choices.values()):
        _issue(request, "invalid_appointment", "", "预约馆区需从原需求的选项中确认。")
        return
    for source, place in choices.items():
        item = next((a for a in request.appointments if a["source"] == source), None)
        if (not item or place not in item["place_options"] or not isinstance(item["day"], int)
                or not 1 <= item["day"] <= request.days or not item["start_time"] or not item["end_time"]):
            _issue(request, "invalid_appointment", source, "预约地点或天数与原需求不匹配。")
            continue
        if any(other["confirmed"] and other["day"] == item["day"] and
               item["start_time"] < other["end_time"] and other["start_time"] < item["end_time"]
               for other in request.appointments):
            _issue(request, "overlapping_appointments", source, "已确认的预约时段相互重叠。")
            continue
        item.update(place=place, confirmed=True)
        request.issues[:] = [issue for issue in request.issues if not (
            issue["code"] == "appointment_requires_confirmation" and issue["source"] == source)]
        request.evidence.append(dict(field="appointments", value=place, source=source))


def _places(request, env, clauses):
    anaphora = bool(re.search(r"这(?:两处|几处|些地方|家).*?(?:一定|必须|要排)", request.nature_language))
    for kind in ("attractions", "restaurants"):
        names = _catalog(env, request.target_city, kind)
        mapping = aliases(names)
        for clause in clauses:
            if any(item["source"] == clause for item in request.appointments):
                continue
            matches = [(alias, choices) for alias, choices in mapping.items()
                       if len(alias) >= 2 and alias in clause]
            # A full venue name takes priority over substrings in that name.
            matches = [(alias, choices) for alias, choices in matches
                       if not any(alias != other and alias in other for other, _ in matches)]
            for alias, choices in matches:
                if re.search(re.escape(alias) + r"(?:附近|旁边|周边)", clause):
                    continue
                if NEGATIVE.search(clause) and re.search(r"最好|尽量|尽可能|如果", clause):
                    _issue(request, "unscored_soft_exclusion", clause,
                           "这项避免前往的软偏好已保留，当前没有直接计入评分，也未设为硬排除", False)
                    continue
                if len(choices) != 1:
                    _issue(request, "ambiguous_place", alias, f"存在多个匹配地点，请选择具体地点 {sorted(choices)}")
                    continue
                target = "excluded_" if NEGATIVE.search(clause) else "must_" if STRONG.search(clause) or anaphora or not SOFT.search(clause) else "preferred_"
                field_name = target + kind
                name = next(iter(choices))
                setattr(request, field_name, tuple(dict.fromkeys((*getattr(request, field_name), name))))
                request.evidence.append(dict(field=field_name, value=name, source=clause, token=alias))


def _types(request, env, clauses):
    for kind, column, catalog_kind, alias_kind in (
        ("attraction_types", "type", "attractions", "spot_type"),
        ("restaurant_types", "cuisine", "restaurants", "food_type"),
    ):
        rows = getattr(getattr(env, catalog_kind, None), "data", {}).get(request.target_city)
        available = set() if rows is None or column not in rows else set(rows[column].dropna().astype(str))
        mapping = {value: value for value in available}
        mapping.update({key: value for key, value in TAG_ALIASES.get(alias_kind, {}).items()
                        if value in available})
        for clause in clauses:
            if any(item["source"] == clause for item in request.appointments):
                continue
            # Mask venue names while retaining additional category requests.
            remaining = clause
            for evidence in request.evidence:
                if evidence["source"] == clause and evidence["field"].endswith(("_attractions", "_restaurants")):
                    remaining = remaining.replace(evidence.get("token", evidence["value"]), "")
            for alias, value in sorted(mapping.items(), key=lambda p: -len(p[0])):
                if len(alias) < 2 or alias not in remaining:
                    continue
                negative = bool(re.search(r"不去|别去|不想去|不要去|避免去|不吃|别吃|不想吃|不要吃", clause))
                if negative and re.search(r"最好|尽量|尽可能|如果", clause):
                    _issue(request, "unscored_soft_exclusion", clause,
                           "这项避免某类活动的软偏好已保留，当前没有直接计入评分，也未设为硬排除", False)
                    continue
                target = "excluded_" if negative else "must_" if STRONG.search(clause) else "preferred_"
                field_name = target + kind
                setattr(request, field_name, tuple(dict.fromkeys((*getattr(request, field_name), value))))
                request.evidence.append(dict(field=field_name, value=value, source=clause, token=alias))


def _public_fields(request, query):
    constraints = query.get("constraints", {})
    if constraints is not None and not isinstance(constraints, dict):
        _issue(request, "invalid_constraints", "constraints", "constraints应为对象")
        constraints = {}
    for name in sorted(set(constraints or {}) - FORM_FIELDS, key=str):
        _issue(request, "unsupported_constraint_field", str(name), "该约束字段尚不支持，请确认具体要求")
    for name in sorted(set(query) - PUBLIC_FIELDS - IGNORED_FIELDS, key=str):
        _issue(request, "unsupported_request_field", str(name), "该输入字段尚不支持，请确认具体要求")
    values = {**(constraints or {}), **query}
    for name in ("budget", "hotel_price_limit", "food_price_limit", "rooms", "room_type"):
        if values.get(name) is not None:
            _assign(request, name, values[name], name, integer=name in ("rooms", "room_type"))
    for name in ("outbound_earliest", "outbound_latest", "return_departure_latest", "return_arrival_latest"):
        if values.get(name) is not None:
            _set_clock(request, name, values[name], name)
    for name, allowed in (("intercity_modes", ("train", "airplane")), ("local_modes", ("metro", "taxi", "walk"))):
        if name in values:
            entries = values[name]
            if not isinstance(entries, (tuple, list)) or not entries or any(v not in allowed for v in entries):
                _issue(request, "invalid_modes", name, f"请从 {allowed} 中选择")
            elif set(entries).intersection(getattr(request, name)):
                setattr(request, name, tuple(m for m in entries if m in getattr(request, name)))
            else:
                _issue(request, "conflicting_modes", name, "文本和表单指定的交通方式冲突")
    for prefix in ("must_", "preferred_", "excluded_"):
        for kind in ("attractions", "restaurants", "attraction_types", "restaurant_types"):
            name = prefix + kind
            if name not in values:
                continue
            entries = values[name]
            if not isinstance(entries, (tuple, list)) or any(not isinstance(x, str) or not x.strip() for x in entries):
                _issue(request, "invalid_places", name, "地点应为非空名称列表")
                continue
            setattr(request, name, tuple(dict.fromkeys((*getattr(request, name), *(x.strip() for x in entries)))))


def split_clauses(text):
    """Keep the original wording for evidence and identify clauses consistently."""
    return [part.strip() for part in re.split(r"[，,。；;！!？?\n]|但是|但(?!是)|并且|而且|同时|另外", text) if part.strip()]


def _daily_limits(request, clauses):
    fields = {
        "max_daily_walk_minutes": r"步行|走路",
        "max_daily_transport_minutes": r"市内交通|市内通勤",
        "max_daily_attractions": r"景点|景区",
    }
    upper = r"最多|(?:不能|不得|不|不要|别)超过|不多于|上限(?:为|是)?"
    for clause in clauses:
        if not re.search(r"每天|每日|一天", clause):
            continue
        if re.search(r"最好|尽量|尽可能", clause) and not STRONG.search(clause):
            continue
        for field, terms in fields.items():
            if not re.search(terms, clause):
                continue
            unit = r"个|处" if field == "max_daily_attractions" else r"分钟|小时"
            match = re.search(rf"(?:{upper})\s*(?:{terms})?\s*({NUMBER})\s*({unit})", clause)
            if not match:
                match = re.search(rf"({NUMBER})\s*({unit})\s*(?:以内|以下)", clause)
            if not match:
                continue
            value = _number(match[1]) * (60 if match[2] == "小时" else 1)
            limits = request.profile.setdefault("hard_limits", {})
            if not isinstance(limits, dict):
                _issue(request, "invalid_profile", clause, "每日限制应为对象")
                return
            if field in limits and limits[field] != value:
                _issue(request, "conflicting_values", clause, "每日限制与表单不一致，请确认")
                continue
            limits[field] = value
            request.evidence.append(dict(field=field, value=value, source=clause))


def _unresolved(request, clauses):
    appointment_sources = {a["source"] for a in request.appointments}
    represented = {e["source"] for e in request.evidence} | appointment_sources
    for clause in clauses:
        for feature, pattern in (("culture", r"文化类?景点|文化类?场馆"),
                                 ("first_day_sightseeing", r"第一天.*(?:游览|逛|景点)")):
            if re.search(pattern, clause) and re.search(r"想|希望|更好|尽量|最好", clause) and not STRONG.search(clause):
                request.preference_dimensions = tuple(dict.fromkeys((*request.preference_dimensions, feature)))
                request.evidence.append(dict(field=feature, value=True, source=clause))
        reference = re.search(rf"这({NUMBER})(?:处|个地方).*?(?:一定|必须|要安排)", clause)
        if reference and len(request.must_attractions) + len(request.must_restaurants) >= _number(reference[1]):
            represented.add(clause)
        if (re.search(r"轮椅|无障碍|过敏|不能吃|忌口", clause)
            or (clause not in appointment_sources and re.search(r"预约|订好|已订|预订了", clause))):
            _issue(request, "unsupported_requirement", clause, "该条件需要进一步结构化确认，原要求已保留")
        elif re.search(r"必须去|一定要去|非去不可|请把.+排进", clause) and clause not in represented:
            _issue(request, "unresolved_place", clause, "未在目的地数据中识别出要求的地点，请确认名称")
        elif (NEGATIVE.search(clause) and re.search(r"最好|尽量|尽可能|如果", clause)
              and not re.search(r"必须|一定|务必|只能", clause)):
            _issue(request, "unscored_soft_exclusion", clause,
                   "这项软排除偏好已保留，当前没有直接计入评分", False)
        elif re.search(r"必须|只能|禁止|不允许|一定|务必|不去|别去|不想去|不要去|不吃|别吃|不想吃|不要吃", clause) and clause not in represented:
            if not re.search(r"火车|高铁|动车|飞机|地铁|出租车|步行|双床|单床|大床", clause):
                _issue(request, "unresolved_hard_requirement", clause, "该硬要求尚未映射到可核验字段")
        elif (re.search(r"至少|不少于|最多|不超过|上限", clause) and re.search(NUMBER, clause)
              and clause not in represented and not re.search(r"最好|尽量", clause)):
            _issue(request, "unresolved_numeric_requirement", clause, "该数量要求尚未映射到可核验字段")
        if clause in represented and re.search(r"(?:必须|一定要|务必)(?:去|参观|游览|逛)", clause):
            remainder = clause
            for evidence in request.evidence:
                if evidence["source"] == clause and evidence.get("token"):
                    remainder = remainder.replace(evidence["token"], "")
            remainder = re.sub(r"必须|一定要|务必|参观|游览|去|逛|一趟|一次|我们|我|和|以及|并且|也|都|、|\s", "", remainder)
            if remainder:
                _issue(request, "partially_unresolved_requirement", clause,
                       f"同一句中还有未识别的地点或限定内容 {remainder}")
        if SOFT.search(clause) or re.search(r"少换乘|少绕路|少走|不赶|别排.*紧|别安排太满|慢慢逛|宽松", clause):
            request.soft_preferences += (clause,)
    request.soft_preferences = tuple(dict.fromkeys(request.soft_preferences))


def parse_request(query, env=None):
    """Return an inspectable request. Recoverable input errors appear in issues."""
    if not isinstance(query, dict):
        request = PlanningRequest()
        _issue(request, "invalid_request", "", "需求应为JSON对象")
        return request
    raw = query.get("nature_language", "")
    request = PlanningRequest(uid=str(query.get("uid", "")))
    if not isinstance(raw, str):
        _issue(request, "invalid_text", "nature_language", "自然语言需求应为字符串")
        raw = ""
    request.nature_language = raw
    text = unicodedata.normalize("NFKC", raw)
    clauses = split_clauses(text)
    cities = getattr(env, "support_cities", ())
    if not cities:
        from chinatravel.environment.language import city_names
        cities = city_names("zh")
    _basic(request, query, text, cities)
    _amounts(request, clauses)
    _rooms_modes(request, text, clauses)
    _times(request, clauses)
    for clause in clauses:
        add_appointment(request, env, clause)
    _places(request, env, clauses)
    _types(request, env, clauses)
    _public_fields(request, query)
    if isinstance(query.get("profile", {}), dict):
        request.profile = dict(query.get("profile", {}))
        limits = request.profile.get("hard_limits")
        if isinstance(limits, dict):
            request.profile["hard_limits"] = dict(limits)
    else:
        _issue(request, "invalid_profile", "profile", "个性化配置应为对象")
    _daily_limits(request, clauses)
    _unresolved(request, clauses)
    for kind in ("attractions", "restaurants", "attraction_types", "restaurant_types"):
        conflict = set(getattr(request, "must_" + kind)) & set(getattr(request, "excluded_" + kind))
        if conflict:
            _issue(request, "conflicting_places", str(sorted(conflict)), "同一地点同时要求前往和排除")
        if kind.endswith("_types"):
            tool_name = "attractions" if kind == "attraction_types" else "restaurants"
            column = "type" if kind == "attraction_types" else "cuisine"
            rows = getattr(getattr(env, tool_name, None), "data", {}).get(request.target_city)
            known = set() if rows is None or column not in rows else set(rows[column].dropna().astype(str))
        else:
            known = _catalog(env, request.target_city, kind)
        for name in getattr(request, "must_" + kind):
            if env is not None and name not in known:
                _issue(request, "unavailable_place", name, "当前目的地数据没有该地点")
    if request.outbound_earliest and request.outbound_latest and request.outbound_earliest > request.outbound_latest:
        _issue(request, "conflicting_times", "", "去程最早时间晚于最晚时间")
    return request
