"""One-call TripSpec interpretation with evidence and explicit confirmation."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
import json
import math
import os
import re
import time

from .world_facts import day
from .open_requirements import normalize_open_experience


VERSION = 'tripspec_v1'
MODEL = 'deepseek-flash'
TIMEOUT = 12
MAX_TOKENS = 1536

FIELDS = {
    'origin_city': '出发地', 'destination_city': '目的地', 'days': '旅行天数',
    'people': '同行人数', 'start_date': '出发日期', 'date_scope': '日期范围',
    'budget': '预算上限', 'budget_target': '预算目标', 'budget_attitude': '预算倾向',
    'total_cost': '总费用', 'meal_budget': '每餐预算', 'lodging_budget': '住宿预算',
    'room_count': '房间数量', 'room_type': '房型意向', 'room_view': '房间景观',
    'nights': '住宿晚数', 'hotel_feature': '酒店设施', 'hotel': '酒店',
    'hotel_type': '酒店类型', 'lodging_accessibility': '住宿接驳',
    'lodging_in_plan': '安排住宿', 'lodging_cost': '住宿费用', 'cuisine': '菜系',
    'restaurant': '餐馆', 'dish': '菜品', 'food_quality': '餐饮体验',
    'food_cost': '餐饮费用', 'food_spend': '餐饮金额', 'food_spend_share': '餐饮支出占比',
    'restaurant_access_time': '到餐馆的交通', 'meal_accessibility': '吃喝便利',
    'attraction': '景点', 'experience_theme': '体验主题', 'experience_example': '同类景点示例',
    'visit_depth': '重点地点停留', 'pace': '节奏', 'local_mode': '市内交通方式',
    'intercity_mode': '城际交通方式', 'arrival_station': '抵达车站或机场',
    'arrival_time': '抵达时段', 'flight_booking_status': '机票状态', 'meeting_area': '会议区域',
    'transport_accessibility': '交通便利', 'transit_time': '市内交通时间',
    'walking_experience': '步行体验', 'attraction_count': '景点数量',
    'other_attractions': '其他景点', 'near_anchor': '游览区域',
    'crowd_or_popularity': '人流或热门程度', 'comfort_temperature': '气温体感',
    'party_context': '同行情况', 'trip_context': '旅行背景',
    'appointment': '已预约', 'access_walk_limit': '接驳步行上限', 'unmapped': '待核实要求',
}
CORE = ('origin_city', 'destination_city', 'days', 'people')
SCALARS = {
    *CORE, 'start_date', 'budget', 'budget_target', 'meal_budget', 'lodging_budget',
    'room_count', 'room_view', 'nights', 'arrival_station', 'arrival_time',
    'flight_booking_status', 'pace', 'access_walk_limit',
}
COUNTS = {'days', 'people', 'nights', 'room_count'}
MONEY = {'budget', 'budget_target', 'meal_budget', 'lodging_budget'}
OPERATORS = {
    'eq', 'lte', 'approx', 'record', 'relative', 'select', 'prefer', 'require', 'include',
    'accept', 'allowed_set', 'exclude', 'minimize', 'maximize', 'any_of', 'avoid', 'near',
    'example_of', 'prioritize', 'prefer_longer', 'minimize_distance', 'expected', 'no_preference',
    'unspecified',
}

FORM_FIELDS = {
    'start_city': ('origin_city', 'eq', None, 'total_trip', 'fact'),
    'target_city': ('destination_city', 'eq', None, 'total_trip', 'fact'),
    'days': ('days', 'eq', 'day', 'total_trip', 'fact'),
    'people_number': ('people', 'eq', 'person', 'total_trip', 'fact'),
    'start_date': ('start_date', 'eq', None, 'total_trip', 'fact'),
    'budget': ('budget', 'lte', 'CNY/group', 'total_trip', 'hard'),
    'intercity_modes': ('intercity_mode', 'select', None, 'round_trip', 'hard'),
    'local_modes': ('local_mode', 'select', None, 'local_travel', 'hard'),
    'rooms': ('room_count', 'eq', 'room', 'lodging', 'hard'),
    'room_type': ('room_type', 'record', None, 'lodging', 'fact'),
}
PROFILE_FIELDS = {
    'interests': ('experience_theme', 'local_experience', 'prefer', 'soft'),
    'cuisines': ('cuisine', 'food', 'prefer', 'soft'),
    'pace': ('pace', 'local_experience', 'prefer', 'soft'),
    'hotel_features': ('hotel_feature', 'lodging', 'prefer', 'soft'),
    'required_hotel_features': ('hotel_feature', 'lodging', 'require', 'hard'),
    'avoid_interests': ('experience_theme', 'local_experience', 'avoid', 'soft'),
    'preferred_places': ('attraction', 'local_experience', 'prefer', 'soft'),
    'anchor': ('near_anchor', 'local_experience', 'minimize_distance', 'soft'),
}
ALIASES = {
    'train': '火车', 'airplane': '飞机', 'rail': '火车', 'flight': '飞机',
    'high_speed_rail': '高铁', 'metro': '地铁', 'taxi': '出租车', 'walk': '步行',
    'museum': '博物馆', 'history': '历史文化', 'nature': '自然风光', 'park': '公园',
    'city': '城市街区', 'amusement': '游乐体验', '打车':'出租车', '走路':'步行',
    'relaxed': '舒缓', 'balanced': '均衡', 'full': '充实',
}
MODE_FIELDS = {'intercity_mode', 'local_mode'}
RELATIVE_START = re.compile(r'今天|明天|后天|(?:本周|下周)[一二三四五六日天末]?')
RELATIVE_DATE_QUESTION = '请填写“明天”或相对日期对应的出发日期。'


def relative_start(value):
    return isinstance(value, str) and RELATIVE_START.fullmatch(value.strip()) is not None


def _calendar_day(value):
    try:
        return day(value)
    except ValueError:
        return None


def canonical(value):
    if isinstance(value, list):
        values = [canonical(v) for v in value]
        return values[0] if len(values) == 1 else values
    return ALIASES.get(value, value) if isinstance(value, str) else value


def label_tuple(field, operator, unit, scope, strength, value, source):
    return dict(field=field, label=FIELDS[field], operator=operator, unit=unit, scope=scope,
                strength=strength, value=canonical(value), source=source, additional_sources=[],
                expected_action='record')


def public_input(payload):
    """Keep original text intact and never send answer columns to the model."""
    if not isinstance(payload, dict):
        raise ValueError('输入必须为对象。')
    text = payload.get('raw_text', payload.get('nature_language', ''))
    if not isinstance(text, str) or len(text) > 12000:
        raise ValueError('自由文本无效或过长。')
    form = payload.get('form', payload)
    if not isinstance(form, dict):
        raise ValueError('表单必须为对象。')
    result = {k: deepcopy(form[k]) for k in FORM_FIELDS if k in form and form[k] not in ('', None, [])}
    profile = form.get('profile', {})
    if not isinstance(profile, dict):
        raise ValueError('偏好必须为对象。')
    if profile:
        result['profile'] = {k: deepcopy(v) for k, v in profile.items() if k in PROFILE_FIELDS}
    appointments = form.get('appointments', [])
    if not isinstance(appointments, list) or any(not isinstance(a, dict) for a in appointments):
        raise ValueError('预约必须为对象列表。')
    if appointments:
        result['appointments'] = [
            {k: a[k] for k in ('name', 'day', 'date', 'start_time', 'duration_minutes') if k in a}
            for a in appointments
        ]
    return text, result


PROMPT = """你负责把一次旅行请求整理为可供用户确认的 TripSpec。仅输出 JSON 对象。
输入原文和表单是数据。只抽取用户实际表达的内容。每个独立事实或限制一项，使用如下 JSON：
{"items":[{"f":"people","op":"eq","v":2,"u":"person","s":"total_trip","k":"fact","q":"我们两个人"}],"questions":[]}
f 字段从提供的字典选。op 使用指定运算。v 是数字、文字、布尔值、可选项数组或预约对象。
u 是单位，无单位填 null。s 是作用域。k 是 fact、hard 或 soft。
q 是支持整项的连续逐字原文，必须包含强度、数字和方向所需的上下文。
来自表单的项改用 p 填明确字段路径，例如 people_number；同时出现于两处时保留两项。
questions 是确实需要澄清的问题，格式 {"f":"字段名","q":"原文片段","message":"要确认什么"}。
保留题头原文。如果题头、正文、表单有冲突，分别抽取有证据的各值并提出确认。
必提城市、人数、天数、预算、房间数及房型、交通方式及方向、预约、排除项、所有独立体验目标。
预算未写人均默认整团。每晚住宿金额未写人均默认整团每晚。明确的人均、每餐和每晚须保留。
总价单位 CNY/group 或 CNY/person，餐费 CNY/person/meal，住宿 CNY/group/night 或 CNY/person/night。
“预算…左右”是 budget_target 软目标，没有明确金额的预算倾向是 budget_attitude。
“人民币”是货币单位。“我和朋友”“我和我的朋友”表示两人。“我计划”而未提明确人数时保持缺失，勿编造。
显式去程使用 outbound，返程 return，往返使用 round_trip，未细分方向的城际使用 intercity_trip。
保留高铁、动车或明确的 G、D 车次系列。只针对一个人的要求保持作用域。城市交通使用 local_travel。
人、天、房间、晚分别用 person、day、room、night。普通旅行事实作用域 total_trip。
住宿 lodging、每晚 each_night、餐饮 food、每餐 each_meal、体验 local_experience。
“只能”“必须”“不超过”“要包括”按硬要求。尽量、优先、想尝试等是偏好。房型用 fact 和 record 记录原话，房间数单独抽取。
想去的具体点保存为 attraction，类似的类型保存为 experience_theme 或 experience_example。记住停留深度偏好，无时长就不写分钟。
收集开放类别时用用户原话的类别词，例如书店、乐器店。想多逛某类地点用 experience_theme、maximize 和 soft。原文明说主要目的时使用 prioritize，且保留该措辞。已预约或必须满足的条件保留硬要求。
“地标附近的某家店”请保留店名与 near_anchor 的原文，店名的 q 包含修饰词。独立的距离、时长或地点限制另存，保留原文强度。未知要求保留原文并用 unmapped。
并列景点分项。选择关系用 any_of 数组。多去景点、少接驳、到餐馆接驳、餐饮花费金额与费用占比各用对应字段。
没有日期就留空。“明天”之类保留原词。只有行程与固定日历日期的事项需要对齐时才追问出发日期。
预约值包含 name、start_time、duration_minutes，以及明确的行程日 day 或 YYYY-MM-DD 的 date。不要给只有日历日期的预约猜行程日。
解释不清的原话放入 unmapped 并保留 q。输出尽量紧凑，无长篇分析。"""


@dataclass
class ModelReply:
    draft: dict | None = None
    attempts: int = 0
    elapsed_seconds: float = 0
    usage: dict = field(default_factory=dict)
    error: str | None = None
    model: str | None = None
    invalid_content: str | None = None


def extract_api(text, form, cities, api_key=None, *, provider=None):
    if not text.strip():
        return ModelReply(draft={'items': [], 'questions': []})
    submit = provider or request_deepseek_json
    authentication = {'api_key': api_key} if provider is None else {}
    reply = submit(
        PROMPT, {'fields': FIELDS, 'operators': sorted(OPERATORS), 'cities': sorted(cities),
                 'raw_text': text, 'form': form}, max_tokens=MAX_TOKENS, **authentication,
    )
    if reply.draft is not None:
        try:
            reply.draft = read_draft(reply.draft)
        except ValueError:
            reply.draft = None
            reply.error = 'invalid_model_output'
    return reply


def request_deepseek_json(prompt, payload, *, max_tokens, api_key=None, timeout=TIMEOUT, capture_invalid=False):
    """Send one structured request and report transport, content and usage separately."""
    key = api_key or os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        return ModelReply(error='missing_model_credentials')
    from openai import APIError, OpenAI

    start = time.perf_counter()
    reply = ModelReply(attempts=1, model=MODEL)
    try:
        with OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=timeout, max_retries=0,
                    default_headers={'Accept-Encoding': 'identity'}) as client:
            response = client.chat.completions.create(
                model=MODEL, temperature=0, max_tokens=max_tokens,
                extra_body={'thinking': {'type': 'disabled'}}, response_format={'type': 'json_object'},
                messages=[
                    {'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
                ],
            )
        reply.model = response.model
        if response.usage:
            raw_usage = response.usage.model_dump()
            reply.usage = {k: raw_usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                           'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens') if raw_usage.get(k) is not None}
        choice = response.choices[0] if response.choices else None
        if not choice or choice.finish_reason == 'length' or not (choice.message.content or '').strip():
            reply.error = 'truncated_or_empty_model_output'
        else:
            content = choice.message.content
            try:
                value = json.loads(content)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                reply.draft = value
            else:
                reply.error = 'invalid_model_output'
                if capture_invalid:
                    reply.invalid_content = content
    except APIError as exc:
        reply.error = type(exc).__name__
    except (ValueError, TypeError, KeyError, AttributeError):
        reply.error = 'invalid_model_output'
    finally:
        reply.elapsed_seconds = round(time.perf_counter() - start, 5)
    return reply


def read_draft(output):
    if not isinstance(output, dict) or not isinstance(output.get('items'), list):
        raise ValueError('模型未返回可解析的需求列表。')
    questions = output.get('questions', [])
    if not isinstance(questions, list):
        raise ValueError('模型的待确认项不是列表。')
    return {'items': output['items'][:200], 'questions': questions[:50]}


def evidence(text, quote):
    pos = text.find(quote) if isinstance(quote, str) and quote.strip() else -1
    return {'type': 'original_text', 'text': quote, 'start': pos, 'end': pos + len(quote)} if pos >= 0 else None


def model_item(raw, text):
    if not isinstance(raw, dict) or any(not isinstance(raw.get(k),str) for k in ('f','op','k','s')):
        return None
    if raw['f'] not in FIELDS or raw['op'] not in OPERATORS:
        return None
    if raw.get('k') not in ('fact', 'hard', 'soft') or not isinstance(raw.get('s'), str):
        return None
    source = evidence(text, raw.get('q')) or {'type': 'unverified', 'text': raw.get('q')}
    if isinstance(raw.get('p'), str):
        source = {'type': 'form', 'path': raw['p']}
    unit = None if raw['f'] in ('origin_city', 'destination_city') else raw.get('u')
    return label_tuple(raw['f'], raw['op'], unit, raw['s'], raw['k'], raw.get('v'), source)


def form_items(form):
    items = []
    for key, values in FORM_FIELDS.items():
        if key in form:
            items.append(label_tuple(*values, form[key], {'type': 'form', 'path': key}))
    for key, value in form.get('profile', {}).items():
        name, scope, op, strength = PROFILE_FIELDS[key]
        for v in value if isinstance(value, list) else [value]:
            items.append(label_tuple(name, op, None, scope, strength, v, {'type': 'form', 'path': f'profile.{key}'}))
    for i, entry in enumerate(form.get('appointments', [])):
        items.append(label_tuple('appointment', 'include', None, 'appointment', 'hard', entry,
                                 {'type': 'form', 'path': f'appointments[{i}]'}))
    return items


def issue(code, name, message, choices=None):
    result = {'code': code, 'field': name, 'message': message, 'blocking': True}
    if choices:
        result['choices'] = choices
    return result


def validated_value(item, cities):
    name, value = item['field'], item['value']
    if name in ('origin_city', 'destination_city'):
        return isinstance(value, str) and value in cities
    if name in COUNTS:
        return type(value) is int and (value >= 0 if name == 'nights' else value >= 1)
    if name == 'start_date':
        return relative_start(value) or _calendar_day(value) is not None
    if name in MONEY:
        return (type(value) in (int, float) and math.isfinite(value) and value >= 0
                and item['unit'] in {'CNY/group', 'CNY/person', 'CNY/group/night',
                                     'CNY/person/night', 'CNY/person/meal', 'CNY/night'})
    if name == 'appointment':
        def valid_appointment_day(event):
            if 'date' in event and _calendar_day(event['date']) is None:
                return False
            if 'day' in event:
                return type(event['day']) is int and event['day'] >= 1
            return 'date' in event

        return (isinstance(value, dict) and isinstance(value.get('name'), str) and value['name'].strip()
                and valid_appointment_day(value)
                and isinstance(value.get('start_time'), str)
                and bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value['start_time']))
                and type(value.get('duration_minutes')) is int and value['duration_minutes'] >= 1)
    if name in MODE_FIELDS:
        opts = value if isinstance(value, list) else [value]
        return bool(opts) and all(isinstance(v, str) for v in opts) and all(
            re.fullmatch(r'飞机|火车|高铁(?:G)?|动车(?:D)?|地铁|公交|步行|出租车|自驾|骑行|公共交通|网约车|轮渡', v)
            for v in opts
        )
    return ((isinstance(value, str) and bool(value.strip())) or type(value) is bool
            or (isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)))


_ZH_DIGITS = '零一二三四五六七八九'


def count_value(token):
    if token.isdigit():
        return int(token)
    token = token.replace('两', '二').replace('〇', '零')
    if len(token) == 1 and token in _ZH_DIGITS:
        return _ZH_DIGITS.index(token)
    if '十' in token:
        left, right = token.split('十', 1)
        return (count_value(left) if left else 1) * 10 + (count_value(right) if right else 0)
    return None


_COUNT = r'[\d零〇一二两三四五六七八九十]+'
_BUDGET = re.compile(
    r'(?P<before>(?:总|人均|每人)?预算(?:约为|在|大约|为|约|(?:不要|不能|不)超过)?|(?:总?花费|总?费用)(?:不要|不能|不)超过)'
    r'\s*(?P<person>人均|每人)?\s*[¥￥]?\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<scale>万|千)?'
    r'\s*(?:元|人民币)?\s*(?P<tail>左右|以内|以下)?'
)
_BUDGET_EXCLUSION = re.compile(r'\s*[，,（(]?\s*(?:不包括|不包含|不含|不计入)\s*([^，,。；;）)\n]+)[）)]?')
_INTERCITY_EXPENSE = r'(?:(?:往返|来回)?(?:车票(?:费)?|机票(?:费)?|城际交通(?:费)?|大交通(?:费)?))'
_LODGING_EXPENSE = r'(?:住宿(?:费|费用)?|酒店(?:费|费用)?|房费)'
_EXPENSE_JOIN = r'\s*(?:和|及|以及|与|、|加上)?\s*'
BUDGET_SCOPE_LABELS = {
    'without_intercity': '预算上限（不含城际车票）',
    'without_lodging': '预算上限（不含住宿）',
    'without_intercity_and_lodging': '预算上限（不含城际车票和住宿）',
    'unresolved_budget_scope': '预算上限（费用范围待确认）',
}


def _budget_scope(text, end):
    match = _BUDGET_EXCLUSION.match(text, end)
    if not match:
        return 'total_trip', end
    fees = match[1].strip()
    if re.fullmatch(_INTERCITY_EXPENSE, fees):
        scope = 'without_intercity'
    elif re.fullmatch(_LODGING_EXPENSE, fees):
        scope = 'without_lodging'
    elif re.fullmatch(rf'(?:{_INTERCITY_EXPENSE}{_EXPENSE_JOIN}{_LODGING_EXPENSE}'
                      rf'|{_LODGING_EXPENSE}{_EXPENSE_JOIN}{_INTERCITY_EXPENSE})', fees):
        scope = 'without_intercity_and_lodging'
    else:
        scope = 'unresolved_budget_scope'
    return scope, match.end()


def _restore_budget_scope(item, text):
    source = item['source']
    if item['field'] not in ('budget', 'budget_target') or source['type'] != 'original_text':
        return item
    quote = source.get('text', '')
    start, end = source.get('start'), source.get('end')
    amount = _BUDGET.match(quote)
    if not amount or not isinstance(start, int) or not isinstance(end, int) or text[start:end] != quote:
        return item
    scope, end = _budget_scope(text, start + amount.end())
    if scope not in BUDGET_SCOPE_LABELS:
        return item
    label = BUDGET_SCOPE_LABELS[scope]
    if item['field'] == 'budget_target':
        label = label.replace('预算上限', '预算目标')
    return {**item, 'scope': scope, 'label': label,
            'source': {**source, 'text': text[start:end], 'end': end}}


def explicit_text(text, cities):
    """A few literal invariants protect salient values from silent model omission."""
    result = []

    def add(f, op, v, u, s, k, match):
        result.append(label_tuple(f, op, u, s, k, v, evidence(text, match)))

    city_names = '|'.join(re.escape(c) for c in sorted(cities, key=len, reverse=True))
    if city_names:
        suffix=r'(?=$|[，,。；;\s\]！!？?]|玩|游玩|旅游|旅行|度假|出差|过周末|出发|过来|[去到往])'
        for f, cue in [('origin_city', '当前位置|从|坐标'), ('destination_city', '目标位置|去|前往|飞往|到')]:
            for m in re.finditer(rf'(?:{cue})\s*({city_names})(?:市)?{suffix}', text):
                add(f, 'eq', m[1], None, 'total_trip', 'fact', m[0])
    for m in re.finditer(rf'旅行人数({_COUNT})|({_COUNT})个人(?!民币)|一家({_COUNT})口', text):
        value = count_value(next(g for g in m.groups() if g is not None))
        if value:
            add('people', 'eq', value, 'person', 'total_trip', 'fact', m[0])
    for m in re.finditer(rf'我(?:和|与)(?:我的)?(?:({_COUNT})(?:位|个))?朋友(?!们)', text):
        if re.match(rf'{_COUNT}(?:个)?人', text[m.end():]):
            continue
        value = 1 + (count_value(m[1]) if m[1] else 1)
        if value:
            add('people', 'eq', value, 'person', 'total_trip', 'fact', m[0])
    for m in re.finditer(rf'旅行天数\s*({_COUNT})|(?:玩|游玩|旅游|行程|大概)\s*({_COUNT})\s*[天日]|(?:游玩|旅游)一周', text):
        digits = next((g for g in m.groups() if g is not None), None)
        value = count_value(digits) if digits else 7
        if value:
            add('days', 'eq', value, 'day', 'total_trip', 'fact', m[0])
    for m in _BUDGET.finditer(text):
        start = text[max(0, m.start()-12):m.start()]
        if re.search(r'每晚|每顿|每餐|(?:酒店|住宿)(?:的)?$', start):
            continue
        unit = 'CNY/person' if m['person'] or m['before'].startswith(('人均', '每人')) else 'CNY/group'
        value = float(m['n']) * {'万': 10000, '千': 1000, None: 1}[m['scale']]
        value = int(value) if value.is_integer() else value
        approximate = m['tail'] == '左右' or '约' in m['before']
        quote = m[0].rstrip()
        source = {'type': 'original_text', 'text': quote, 'start': m.start(), 'end': m.start() + len(quote)}
        item = label_tuple('budget_target' if approximate else 'budget', 'approx' if approximate else 'lte',
                           unit, 'total_trip', 'soft' if approximate else 'hard', value, source)
        result.append(_restore_budget_scope(item, text))
    for m in re.finditer(r'(?:选择|只坐|只能坐)(飞机|火车|高铁(?:[（(]G[)）])?|动车(?:[（(]D[)）])?)出行', text):
        mode = re.sub(r'[（()）]', '', m[1])
        prefix=text[max(0,m.start()-2):m.start()]
        negated=prefix in ('不要','不想')
        add('intercity_mode', 'exclude' if negated else 'select', mode, None, 'intercity_trip', 'hard',
            prefix+m[0] if negated else m[0])
    for m in re.finditer(rf'住\s*({_COUNT})\s*晚',text):
        value=count_value(m[1])
        if value:
            add('nights','eq',value,'night','lodging','hard',m[0])
    for m in re.finditer(r'(?:推荐|安排|规划)(?:一下)?(?:行程和住宿|行程及住宿|行程与住宿|住宿)',text):
        if not re.search(r'不[用需要]|无需',text[max(0,m.start()-5):m.start()]):
            add('lodging_in_plan','include',True,None,'lodging','hard',m[0])
    return result


def normalize_experience_example(item):
    source = item['source']
    value = item['value']
    if source['type'] != 'original_text' or not isinstance(value, str):
        return item
    evidence_text = source.get('text', '')
    if item['field'] == 'experience_example' and item['operator'] == 'eq' and item['strength'] == 'fact':
        if re.search(re.escape(value) + r'(?:这样|这类|这种|类似)', evidence_text):
            return {**item, 'operator': 'example_of'}
    if item['field'] == 'experience_theme' and item['operator'] == 'eq' and item['strength'] == 'fact':
        if re.search(r'想(?:去|看|体验).+(?:这样的|这类|这种)' + re.escape(value), evidence_text):
            return {**item, 'operator': 'prefer', 'strength': 'soft'}
    return item


def normalize_proposal(a, text):
    """Correct representational differences using the words in its own evidence."""
    source=a['source']
    if source['type']!='original_text':
        return a
    a = normalize_experience_example(a)
    q=source['text']
    f=a['field']
    clause=re.split(r'[，,。；;\n]',text[:source['start']])[-1]+q
    if f=='people' and re.search(r'我带我孩子|我带着孩子',q) and not re.search(r'旅行人数|\d+人|[一二两三四五六七八九十]+个',q):
        return None
    if f == 'room_type':
        a.update(scope='lodging', strength='fact', operator='record')
    elif f == 'room_count':
        soft=bool(re.search('最好|尽量|就行|也可以|优先', clause))
        a.update(scope='lodging', strength='soft' if soft else 'hard', operator='eq', unit='room')
    elif f=='intercity_mode':
        normalized=canonical(a['value'])
        candidates=normalized if isinstance(normalized,list) else [normalized]
        candidates=[re.sub(r'[（()）\s]','',v) if isinstance(v,str) else v for v in candidates]
        if not re.search(r'火车|飞机|航空|航班|高铁|动车|铁路|飞往',q):
            return None
        if candidates==['round_trip']:
            return None
        if len(candidates)==1 and candidates[0] in ('G','D','G车次','D车次'):
            candidates=['高铁G' if candidates[0].startswith('G') else '动车D']
        a['value']=canonical(candidates)
        if re.search(r'往返|来回',q):
            if re.match(r'(?:她|他)',q):
                a['scope']='round_trip/referenced_companion'
            elif a['scope']!='round_trip/referenced_companion':
                a['scope']='round_trip'
        elif re.search(r'去程|(?:火车|飞机|高铁|动车)(?:[（(][GD][)）])?去|飞往|抵达|到达',q):
            a['scope']='outbound'
        elif re.search(r'返程|回程|回来',q):
            a['scope']='return'
        elif a['scope']=='total_trip':
            a['scope']='intercity_trip'
        if (re.search(r'尽量|优先|想坐|最好',q) or ('或者' in q and '计划坐' in q)) and not re.search('必须|只能',q):
            a.update(strength='soft',operator='prefer')
        else:
            a['strength']='hard'
            if a['operator'] in ('eq','record','require'):
                a['operator']='select'
        for mode, series in [('高铁','G'),('动车','D')]:
            if re.search(rf'{mode}\s*[（(]\s*{series}\s*[）)]',q,re.I):
                values=a['value'] if isinstance(a['value'],list) else [a['value']]
                a['value']=canonical([mode+series if v==mode else v for v in values])
    elif f=='local_mode':
        if a['value']=='时间' and re.search(r'省时间|节省时间|交通时间',q):
            a.update(field='transit_time',label=FIELDS['transit_time'],value='市内交通时间',unit='minute',
                     strength='soft',operator='minimize',scope='local_travel')
            return a
        a['scope']='local_travel'
        if re.search('为主|主要|优先|尽量',q):
            a.update(strength='soft',operator='prefer')
        elif re.search('不用|不坐|不打|不要|避免',q) or a['operator']=='exclude':
            a.update(strength='hard',operator='exclude')
        else:
            a.update(strength='hard',operator='allowed_set' if '只' in q or '选择' in q else 'select')
    elif f=='pace' and re.search(r'轻松|舒缓|不要特种兵|轻快自由行|不要太辛苦|不想太赶|不要太赶|不要太紧',q):
        a.update(value='舒缓',strength='soft',operator='prefer',scope='local_experience')
    elif f=='meal_budget':
        a.update(scope='each_meal',operator='lte',strength='hard')
        if re.search('人均|每人',q):
            a['unit']='CNY/person/meal'
    elif f=='lodging_budget' and re.search('每晚|一晚|一夜',q):
        a.update(unit='CNY/person/night' if re.search('人均|每人',q) else 'CNY/group/night',scope='each_night')
    elif f=='room_view' and re.search('住|房',q):
        a.update(scope='lodging',strength='soft' if re.search('最好|优先|尽量',q) else 'hard',operator='require')
    elif f in ('hotel','hotel_feature') and re.search('住|酒店|宾馆|房',clause):
        soft=bool(re.search(r'最好|尽量|想住|优先|也可以',clause))
        a.update(scope='lodging',strength='soft' if soft else 'hard',
                 operator='prefer' if soft else ('select' if f=='hotel' else 'require'))
    elif f=='nights' and re.search(r'住\s*'+_COUNT+r'\s*晚',q):
        a.update(scope='lodging',strength='hard',unit='night',operator='eq')
    elif f=='lodging_in_plan' and a['value'] is True:
        a.update(scope='lodging',strength='hard',operator='include')
    elif f in ('arrival_station','arrival_time') and re.search('到达|抵达|到站|降落',q):
        a.update(scope='outbound',strength='hard',operator='eq' if f=='arrival_station' else a['operator'])
    elif f=='crowd_or_popularity':
        a.update(scope='local_experience',strength='soft',operator='avoid')
    elif f in ('attraction','restaurant','cuisine','dish','food_quality'):
        if f=='attraction' and a['value'] in ('景区','景点') and re.search('人过于多|人太多|人多',q):
            return None
        if f=='cuisine' and re.search(r'饮食没有要求|吃饭没有要求|餐饮没有要求',q):
            a.update(value=True,strength='fact',operator='no_preference',scope='food')
        elif not re.search(r'必须|一定|要包括|指定|过敏|严禁|不能',clause) and (
                f!='attraction' or re.search(r'想|希望|喜欢|尽量|最好|重点参观',clause)):
            a.update(strength='soft',operator='prefer')
    return a


def slot(item):
    f, scope = item['field'], item['scope']
    if f in ('budget', 'budget_target'):
        return f, scope
    if f in SCALARS:
        return f, 'single'
    if f in MODE_FIELDS and item['strength'] == 'hard' and item['operator'] in ('select', 'allowed_set'):
        return f, 'round_trip' if scope in ('intercity_trip', 'round_trip') else scope
    return None


def equal_value(a, b):
    if a['unit'] != b['unit'] or a['value'] != b['value']:
        return False
    return a['strength'] == b['strength'] or a['field'] in CORE


def merge(items, text):
    grouped = defaultdict(list)
    other = []
    for item in items:
        if key := slot(item):
            grouped[key].append(item)
        elif not any(all(a[k] == item[k] for k in ('field','value','scope','operator','strength')) for a in other):
            other.append(item)
    output, issues = [], []
    for (name, _), choices in grouped.items():
        unique = []
        for a in choices:
            b = next((part for part in unique if equal_value(a, part)), None)
            if b is None:
                unique.append(deepcopy(a))
            elif a['source'] != b['source'] and a['source'] not in b['additional_sources']:
                b['additional_sources'].append(a['source'])
        if len(unique) > 1 and any(a['strength'] in ('hard','fact') for a in unique):
            issues.append(issue('conflicting_values', name, f'请确认{FIELDS[name]}采用哪一项。', unique))
        else:
            output.extend(unique)
    return output + other, issues


def final_spec(text, form, items, issues, reply, revision=1):
    for i, problem in enumerate(issues, 1):
        problem['id'] = f'q{i}'
    return {'schema_version': VERSION, 'request_revision': revision, 'raw_text': text, 'form': form,
            'items': items, 'issues': issues,
            'state': 'needs_confirmation' if issues else 'ready', 'ready': not issues,
            'model': {'attempts': reply.attempts, 'elapsed_seconds': reply.elapsed_seconds,
                      'usage': reply.usage, 'error': reply.error, 'name': reply.model}}


def check_appointments(items, issues):
    days = next((a['value'] for a in items if a['field']=='days'), None)
    start_date = _calendar_day(next((a['value'] for a in items if a['field'] == 'start_date'), None))
    intervals = defaultdict(list)
    for a in (a for a in items if a['field']=='appointment'):
        entry = a['value']
        if 'date' in entry:
            fixed_date = _calendar_day(entry['date'])
            if not fixed_date:
                issues.append(issue('invalid_appointment_window', 'appointment', '请核对预约的具体日期。'))
                continue
            if not start_date:
                issues.append(issue('appointment_date_needs_start', 'start_date',
                                    f'请填写出发日期，以确认 {entry["name"]} 的预约在行程第几天。'))
                continue
            appointment_day = (fixed_date - start_date).days + 1
            if appointment_day < 1 or entry.get('day', appointment_day) != appointment_day:
                issues.append(issue('invalid_appointment_window', 'appointment',
                                    f'{entry["name"]} 的预约日期与行程日不一致。'))
                continue
            a['value'] = entry = {**entry, 'day': appointment_day}
        day, start = entry['day'], entry['start_time']
        hh, mm = map(int, start.split(':'))
        begin, end = hh*60 + mm, hh*60 + mm + entry['duration_minutes']
        if (days and day > days) or end > 1440 or any(begin < finish and end > prev for prev, finish in intervals[day]):
            issues.append(issue('invalid_appointment_window', 'appointment','请检查预约日序或重叠的时段。'))
        intervals[day].append((begin, end))


def evaluate_draft(text, form, cities, reply, *, checked=True, reference_date=None):
    draft = reply.draft or {'items': [], 'questions': []}
    proposals = [a for raw in draft['items'] if (a := model_item(raw, text))]
    issues = []
    if reply.error:
        messages = {'missing_model_credentials':'模型 API 尚未配置，表单信息已保留。',
                    'local_model_unavailable':'无法连接本地 Qwen，请检查服务后重试。',
                    'local_model_http_error':'本地 Qwen 未能处理本次需求，请重试或切换规划方式。',
                    'APITimeoutError':'理解需求超时，已填写的信息保留，可以重试。'}
        issues.append(issue(reply.error, 'unmapped', messages.get(reply.error,'未取得完整模型回复，请重试或继续填写表单。')))
    if checked or not text.strip():
        items = form_items(form)
        items.extend(explicit_text(text, cities))
        literal_budgets = [a['source'] for a in items if a['field'] in ('budget','budget_target')
                           and a['source']['type']=='original_text']
        normalized=[b for a in proposals if a['source']['type']!='form' and (b:=normalize_proposal(a,text))]
        alternative_modes=[a for a in normalized if a['field']=='intercity_mode'
                           and isinstance(a['value'],list) and a['source']['type']=='original_text'
                           and re.search(r'或者|或',a['source']['text']) and validated_value(a,cities)]
        for a in normalized:
            if a['field']=='intercity_mode' and a['source']['type']=='original_text' and any(
                a['value'] in bundle['value'] and a['source']['text'] != bundle['source']['text']
                and a['source']['text'] in bundle['source']['text'] for bundle in alternative_modes
            ):
                continue
            if a['field'] in ('budget','budget_target','total_cost') and a['source']['type'] == 'original_text':
                source = a['source']
                if any(source['start'] < b['end'] and b['start'] < source['end'] for b in literal_budgets):
                    continue
            if a['source']['type'] == 'unverified' or not validated_value(a, cities):
                if a['field'] != 'room_type' and a['strength'] in ('hard','fact'):
                    issues.append(issue('unverified_requirement', a['field'], f'请检查{a["label"]}的来源或值。'))
                continue
            items.append(a)
        if not any(a['field']=='people' for a in items) and re.search(r'我(?:计划|打算)去',text):
            if not re.search(r'朋友|同学|家人|一起|同行|我们',text):
                person=label_tuple('people','eq','person','total_trip','fact',1,{'type':'assumption'})
                person.update(label='同行人数（可修改默认值）',expected_action='assumption')
                items.append(person)
        valid = []
        for a in items:
            if validated_value(a,cities):
                valid.append(a)
            else:
                issues.append(issue('invalid_field',a['field'], f'请核对{a["label"]}。'))
        for item in valid:
            if item['field'] != 'start_date':
                continue
            offset = {'今天': 0, '明天': 1, '后天': 2}.get(item['value']) if isinstance(item['value'], str) else None
            if offset is not None and reference_date is not None:
                item['value'] = (reference_date + timedelta(days=offset)).isoformat()
        items, conflicts = merge(valid, text)
        issues.extend(conflicts)
        check_appointments(items,issues)
    else:
        items = proposals
    for q in draft['questions']:
        if not isinstance(q,dict) or not isinstance(q.get('message'),str) or q.get('f') not in FIELDS:
            continue
        name = q['f']
        if checked and name == 'room_type':
            continue
        if checked and (not evidence(text,q.get('q')) or any(a['field']==name for a in items)):
            continue
        if not any(a['field']==name for a in issues):
            issues.append(issue('clarification',name,q['message'][:300]))
    for f in CORE:
        if not any(a['field']==f and a['value'] is not None for a in items) and not any(a['field']==f for a in issues):
            issues.append(issue('missing_required_field', f, f'请填写{FIELDS[f]}。'))
    return final_spec(text, form, items, issues, reply)


def interpret(payload, cities, provider=extract_api, *, reference_date=None):
    text, form = public_input(payload)
    reply = provider(text, form, cities) if text.strip() else ModelReply()
    return normalize_saved_spec(evaluate_draft(text, form, cities, reply, reference_date=reference_date))


def normalize_saved_spec(spec):
    """Apply current planning rules to stored confirmations without editing the original."""
    def room_note(item):
        return {**item, 'label': FIELDS['room_type'], 'strength': 'fact', 'operator': 'record'}

    if spec.get('schema_version') != VERSION:
        return spec
    items = [room_note(a) if a['field'] == 'room_type' else
             _restore_budget_scope(normalize_experience_example(a), spec.get('raw_text', '')) for a in spec['items']]
    items = [normalize_open_experience(a, spec.get('raw_text', '')) for a in items]
    items = [{**a, 'value': '舒缓'} if a['field'] == 'pace' and a['strength'] == 'soft'
             and a['operator'] == 'prefer' and a['value'] in ('不要太赶', '不想太赶') else a for a in items]
    for n,item in enumerate(items):
        quote=item['source'].get('text') or ''
        if (item['field'] == 'experience_theme' and item['strength'] == 'soft'
                and item['operator'] == 'prefer' and item['source']['type'] == 'original_text'
                and item['value'] in ('观赏景点', '参观景点', '游览景点')
                and re.fullmatch(r'(?:哪里|有什么|有哪些)?(?:比较|更)?(?:适合)?(?:去|想)?'
                                 + re.escape(item['value']) + r'[？?]?', quote)):
            items[n] = {**item, 'field': 'trip_context', 'label': FIELDS['trip_context'],
                        'strength': 'fact', 'operator': 'record'}
            continue
        if (item['field']=='experience_theme' and item['strength']=='fact' and item['operator'] in ('include','eq')
                and item['source']['type']=='original_text' and isinstance(item['value'],str) and item['value'] in quote
                and re.search(r'想去|想看|希望|喜欢',quote) and not re.search(r'必须|一定|不要|不想|不喜欢',quote)):
            items[n]={**item,'strength':'soft','operator':'prefer'}
    relative = any(a['field'] == 'start_date' and relative_start(a['value']) for a in items)
    text = spec.get('raw_text', '')
    if not any(a['field'] == 'intercity_mode' for a in items):
        core = {a['field']:a['value'] for a in items if a['field'] in ('origin_city','destination_city')}
        if len(core)==2:
            phrase = '从'+core['origin_city']+'飞去'+core['destination_city']
            if source := evidence(text, phrase):
                items.append(label_tuple('intercity_mode','select',None,'outbound','hard','飞机',source))
    convenience = re.search(r'市内交通(?:尽可能|尽量|最好)?(?:方便|便利)',text)
    if convenience:
        items = [{**a,'value':True} if a['field']=='transport_accessibility' and a['strength']=='soft'
                 and a['source'].get('text')==convenience[0] else a for a in items]
    city = next((a['value'] for a in items if a['field']=='destination_city'),None)
    area = re.search(r'去([东南西北]'+re.escape(city)+r')',text) if city else None
    issues = []
    for q in spec['issues']:
        if q['code']=='unverified_requirement':
            if q['field']=='local_mode' and convenience and any(
                    a['field']=='transport_accessibility' and a['value'] is True for a in items
            ) and not re.search(r'地铁|出租车|公交|步行|打车|自驾',text):
                continue
            if q['field']=='people':
                stated=[a for a in items if a['field']=='people']
                children=re.search(rf'({_COUNT})(?:个|名)孩子',text)
                if (len(stated)==1 and children and count_value(children[1]) is not None
                        and stated[0]['value']==2+count_value(children[1])
                        and re.search(r'我和(?:老婆|妻子|丈夫|老公)',text)
                        and not re.search(r'爷爷|奶奶|父母|外公|外婆|老人|朋友',text)):
                    continue
            if q['field']=='start_date' and not any(a['field']=='start_date' for a in items):
                q={**q,'input_type':'date','message':'请填写这次出行的具体出发日期，以便保留节日和预约要求。'}
        if (q['field']=='people' and q['code'] in ('missing_required_field','clarification','unverified_requirement')
                and not any(a['field']=='people' for a in items)):
            q={**q,'input_type':'number','message':'这次一共几个人出行？'}
        if q['field'] == 'room_type' and q['code'] == 'conflicting_values' and q.get('choices'):
            items.extend(room_note(a) for a in q['choices'])
        elif not (relative and q['field'] == 'start_date' and q['code'] == 'clarification'
                  and q['message'] == RELATIVE_DATE_QUESTION):
            issues.append(q)
    if area and not any(a['field']=='trip_context' and a['value']=='按全市范围规划' for a in items
                        ) and not any(q['code']=='area_scope_confirmation' for q in issues):
        choice=label_tuple('trip_context','eq',None,'total_trip','fact','按全市范围规划',evidence(text,area[1]))
        q=issue('area_scope_confirmation','destination_city',
                f'“{area[1]}”的具体范围不明确。可以确认按{city}全市规划，或返回输入具体区域。',[choice])
        q['id']='area_scope'
        issues.append(q)
    if any(a['field'] == 'appointment' and 'date' in a['value'] for a in items):
        items = deepcopy(items)
        other_issues = [q for q in issues if q['code'] not in (
            'invalid_appointment_window', 'appointment_date_needs_start')]
        check_appointments(items, other_issues)
        issues = [{**q, 'id': f'q{i}'} for i, q in enumerate(other_issues, 1)]
    ready = not issues and (spec['ready'] or bool(spec['issues']))
    return {**spec, 'items': items, 'issues': issues, 'ready': ready,
            'state': 'ready' if ready else 'needs_confirmation'}


def resolve_conflicts(spec, choices):
    """Apply user choices by issue index to a server-owned revision."""
    if not isinstance(choices,dict) or not choices:
        raise ValueError('请选择待确认字段。')
    updated=deepcopy(spec)
    selected, remaining = [], []
    for q in updated['issues']:
        if q['id'] not in choices:
            remaining.append(q)
            continue
        i=choices[q['id']]
        if q.get('input_type')=='number' and q['field']=='people':
            if type(i) is not int or i<1:
                raise ValueError('请填写有效的同行人数。')
            selected.append(label_tuple('people','eq','person','total_trip','fact',i,
                                        {'type':'user_confirmation','issue':q['id']}))
            continue
        if q.get('input_type')=='date' and q['field']=='start_date':
            if not isinstance(i,str) or _calendar_day(i) is None:
                raise ValueError('请填写有效的出发日期。')
            selected.append(label_tuple('start_date','eq',None,'total_trip','fact',i,
                                        {'type':'user_confirmation','issue':q['id']}))
            continue
        opts=q.get('choices',[])
        if type(i) is not int or not 0<=i<len(opts):
            raise ValueError('确认选项已变化，请重新查看。')
        item=deepcopy(opts[i])
        item['additional_sources'].append({'type':'user_confirmation','issue':q['id']})
        selected.append(item)
    if len(selected)!=len(choices):
        raise ValueError('有待确认字段已经失效。')
    updated['items'].extend(selected)
    remaining=[q for q in remaining if q['code'] not in ('invalid_appointment_window', 'appointment_date_needs_start')]
    check_appointments(updated['items'], remaining)
    for i,q in enumerate(remaining,1):
        q['id']=f'q{i}'
    updated.update(issues=remaining, ready=not remaining, state='needs_confirmation' if remaining else 'ready',
                   request_revision=spec['request_revision']+1)
    return updated
