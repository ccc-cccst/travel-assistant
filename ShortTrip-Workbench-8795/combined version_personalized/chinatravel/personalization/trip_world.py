"""Build a planning snapshot from a confirmed TripSpec."""

from datetime import timedelta
from hashlib import sha256
import json
import re

from chinatravel.agent.nesy_agent.semantic_grounding import CITY_CUISINES, LOCAL_CUISINE, TAG_ALIASES
from .open_requirements import NAMED_PLACES, place_modifiers
from .reference_scope import reference_route
from .tripspec import relative_start
from .world_facts import KINDS, TICKET_OPTIONS, clock_minutes, day, numeric, planning_place, positive_integer, strings


PLACE_FIELDS = {'attraction': 'attraction', 'restaurant': 'restaurant',
                'hotel': 'accommodation', 'near_anchor': None, 'appointment': 'attraction',
                'experience_example': 'attraction'}
PREFERRED_TAGS = {'attraction': 'experience_theme', 'restaurant': 'cuisine', 'accommodation': 'hotel_feature'}
HOTEL_FEATURE_ALIASES = {'可以打牌': '棋牌室', '能健身': '健身室', '可以健身': '健身室',
                         '健身': '健身室', '健身房': '健身室', '可以看电影': '影音房',
                         '能看电影': '影音房', '看电影': '影音房'}


def hotel_feature_options(value):
    tag = hotel_feature_tag(value)
    return ('影音房', '自营影音房') if tag == '影音房' else (tag,)


def theme_name_matches(name, values):
    names={'海洋馆':('海洋馆','海洋世界'),'动物园':('动物园','野生动物世界','野生动物园')}
    return any(token in name for value in values for token in names.get(value,()))
THEME_TAGS = {'历史文化景点': ('历史古迹', '红色景点'), '历史文化': ('历史古迹', '红色景点'),
              '有文化氛围的旅游区': ('文化旅游区',)}
TAG_FIELDS = {'experience_theme': 'spot_type', 'cuisine': 'food_type'}


def hotel_feature_tag(value):
    return HOTEL_FEATURE_ALIASES.get(value.strip(), value.strip())


def preferred_tag_terms(field, values, city):
    """Resolve the same catalog tags for candidate generation and evaluation."""
    aliases = TAG_ALIASES.get(TAG_FIELDS.get(field), {})
    resolved, literal = set(), set()
    for value in values:
        if field == 'cuisine' and LOCAL_CUISINE.fullmatch(value):
            resolved.update(label.casefold() for label in CITY_CUISINES.get(city, ()))
        elif field == 'experience_theme' and value in THEME_TAGS:
            resolved.update(THEME_TAGS[value])
        elif value in aliases:
            resolved.add(aliases[value].casefold())
        elif field == 'hotel_feature':
            resolved.update(tag.casefold() for tag in hotel_feature_options(value))
        else:
            literal.add(value.casefold())
    return resolved, literal


def matches_tags(tags, exact, literal):
    return any(t.casefold() in exact or any(term in t.casefold() for term in literal)
               for t in tags if isinstance(t, str))


def experience_match(place_name, tags, values, city):
    exact, literal = preferred_tag_terms('experience_theme', values, city)
    return (matches_tags(tags, exact, literal) or theme_name_matches(place_name, values)
            or any(len(value) >= 2 and value.casefold() in place_name.casefold() for value in values))


def requested_experience_match(req, name, tags, city):
    if area := req.get('area_term'):
        # A business named after a district is weak evidence for exploring the area itself.
        outdoor = ('景区','风景','自然','公园','街区','步行街','历史街','人文景观','文化旅游区','广场','园林')
        return area in name and any(word in tag for tag in tags for word in outdoor)
    values = [v for v in _values(req) if isinstance(v, str) and v.strip()]
    return experience_match(name, tags, values, city)


def _resolve_named_place(facts, city, text, kind, modifiers):
    result = facts.place_with_suggestions(city, text, kind)
    if not modifiers or not result['records'] or result['status'] == 'found':
        return result
    # A directory entry may add a branch name in parentheses to the exact
    # business name. A single partial search result alone is weaker evidence.
    branches = [row for row in result['records'] if re.split(r'[（(]', row['name'], 1)[0].strip() == text]
    if len(branches) == 1 and result.get('matched_count', len(result['records'])) == 1:
        return {**result, 'status': 'found', 'records': branches,
                'match_basis': 'exact_business_name_with_branch_suffix',
                'modifiers': modifiers}
    return {**result, 'modifiers': modifiers}


POSITIVE = {'eq', 'select', 'prefer', 'require', 'include', 'prioritize', 'accept', 'near',
            'minimize_distance', 'record', 'allowed_set', 'any_of'}
SELECTION = {'eq', 'select', 'require', 'include', 'allowed_set', 'any_of'}


def _values(item):
    value = item['value']
    return tuple(value) if isinstance(value, list) else (value,)


def exemplar_evidence(groundings):
    records = [p for g in groundings if g['status'] == 'found' for p in g['records'] if p['kind'] == 'attraction']
    return ({p['id'] for p in records},
            {tag.casefold() for p in records for tag in p['tags']['values'] if isinstance(tag, str) and tag})


def _ground_places(facts, city, start, days, requirements, modifiers=None):
    groundings = []
    modifiers = modifiers or {}
    by_id = {item['id']: item for item in requirements}
    targets = {}
    for modifier, target in modifiers.items():
        targets.setdefault(target, []).append(by_id[modifier]['value'])
    for item in requirements:
        field = item['field']
        if item['id'] in modifiers:
            continue
        if field not in PLACE_FIELDS or item['operator'] in ('no_preference', 'unspecified'):
            continue
        options = _values(item)
        if field == 'appointment':
            event = item['value']
            if not isinstance(event, dict):
                raise ValueError('预约须包含地点、日序、时刻和停留分钟')
            visit_day = positive_integer(event.get('day'), '预约日序')
            begin = clock_minutes(event.get('start_time'))
            duration = positive_integer(event.get('duration_minutes'), '预约分钟')
            if visit_day > days or begin is None or begin + duration > 1440:
                raise ValueError('预约日序或时段超出行程范围')
            options = (event.get('name'),)
        if not all(isinstance(text, str) and text.strip() for text in options):
            groundings.append({'requirement_id': item['id'], 'status': 'unsupported_place_value', 'records': []})
            continue
        for text in strings(options, '指定地点'):
            selected_id = item['source'].get('place_id') if item['source'].get('type') == 'map_selection' else None
            if field == 'attraction' and selected_id:
                resolved = facts.place(city, selected_id, 'attraction')
                resolved['query'] = text
            elif field in NAMED_PLACES:
                resolved = _resolve_named_place(facts, city, text, PLACE_FIELDS[field], targets.get(item['id'], []))
            else:
                resolved = facts.place_with_suggestions(city, text, PLACE_FIELDS[field])
            entry = {'requirement_id': item['id'], **resolved,
                     'records': [planning_place(p) for p in resolved['records']]}
            if field == 'appointment':
                windows = resolved['records'][0]['opening']['windows_minutes'] if resolved['status'] == 'found' else []
                end = begin + duration
                entry['visit'] = {
                    'day': visit_day, 'date': (start + timedelta(days=visit_day - 1)).isoformat() if start else None,
                    'window_minutes': [begin, end],
                    'window_status': ('within_reference' if any(lo <= begin and end <= hi for lo, hi in windows)
                                      else 'outside_reference' if windows else 'unknown'),
                    'verified_for_date': False,
                }
            groundings.append(entry)
    return groundings


def _ticket_queries(facts, origin, city, start_date, end_date, requirements, limit, unapplied):
    rules = {'outbound': [], 'return': []}
    for item in requirements:
        if item['field'] != 'intercity_mode' or item['strength'] != 'hard':
            continue
        values = _values(item)
        scope, op = item['scope'], item['operator']
        if (scope not in (*rules, 'round_trip', 'intercity_trip', 'total_trip')
                or op not in SELECTION | {'exclude'}
                or not values or any(not isinstance(v, str) or v not in TICKET_OPTIONS for v in values)):
            unapplied.append(item['id'])
            continue
        for direction in ((scope,) if scope in rules else rules):
            rules[direction].append(('exclude' if op == 'exclude' else 'include', values))
    if origin == city:
        return {}
    end_points = {'outbound': (origin, city, start_date), 'return': (city, origin, end_date)}
    return {direction: [facts.intercity(start, finish, travel_date=travel_date, limit=limit,
                                       restrictions=rules[direction], include_reference_fares=True)]
            for direction, (start, finish, travel_date) in end_points.items()}


def _filters(requirements, unapplied):
    result = {'required_features': set(), 'meal_caps': []}
    for item in requirements:
        if item['strength'] != 'hard':
            continue
        values = _values(item)
        op, field = item['operator'], item['field']
        if field == 'hotel_feature':
            if op in {'eq', 'require', 'include'} and all(isinstance(v, str) and v.strip() for v in values):
                result['required_features'].update(hotel_feature_tag(v) for v in strings(values, '酒店设施'))
            else:
                unapplied.append(item['id'])
        elif field == 'meal_budget':
            cap = numeric(item['value'])
            if op == 'lte' and item['unit'] == 'CNY/person/meal' and cap is not None:
                result['meal_caps'].append(cap)
            else:
                unapplied.append(item['id'])
    return result


def _candidate_allowed(record, filters):
    if not record['planning_catalog'] or not record['routable_in_snapshot']:
        return False
    kind, price = record['kind'], record['price']
    if kind == 'attraction':
        return True
    if not price['usable_for_snapshot_budget']:
        return False
    if kind == 'restaurant':
        return all(price['value'] <= cap for cap in filters['meal_caps'])
    return all(set(hotel_feature_options(tag)).intersection(record['tags']['values'])
               for tag in filters['required_features'])


def excluded_visit_ids(requirements, groundings):
    exclusions = {item['id'] for item in requirements if item['field'] == 'attraction'
                  and item['strength'] == 'hard' and item['operator'] == 'exclude'}
    return {g['requirement_id']: g['records'][0] for g in groundings
            if g['requirement_id'] in exclusions and g['status'] == 'found' and len(g['records']) == 1}


def _candidate_lists(facts, city, requirements, groundings, filters, limit):
    positive = [item for item in requirements if item['operator'] in POSITIVE]
    priority_ids = {item['id'] for item in positive}
    examples = {i['id'] for i in requirements if i['field'] == 'experience_example' and i['operator'] == 'example_of'}
    exemplar_ids, exemplar_tags = exemplar_evidence(g for g in groundings if g['requirement_id'] in examples)
    exact_ids = {p['id'] for g in groundings if g['requirement_id'] in priority_ids and g['status'] == 'found'
                 for p in g['records']}
    possible_ids = {p['id'] for g in groundings if g['requirement_id'] in priority_ids
                    and g['status'] in ('ambiguous', 'unconfirmed_match') for p in g['records']}
    excluded_ids = {place['id'] for place in excluded_visit_ids(requirements, groundings).values()}
    candidates, coverage, experiences = {}, {}, {}
    for kind in KINDS:
        rows, usable = facts._catalog(city, kind)
        terms = {v.casefold() for item in positive if item['field'] == PREFERRED_TAGS[kind]
                 for v in _values(item) if isinstance(v, str) and v}
        eligible = [p for row in rows if _candidate_allowed(p := planning_place(facts._place(row)), filters)
                    and p['id'] not in excluded_ids]
        matched_experiences = set()
        if kind == 'attraction':
            for item in requirements:
                if not item.get('open_experience'):
                    continue
                matches = [p for p in eligible if requested_experience_match(item, p['name'], p['tags']['values'], city)]
                experiences[item['id']] = {'matched_count': len(matches),
                                            'records': matches[:limit]}
                matched_experiences.update(p['id'] for p in matches)

        def rank(record):
            rec_id = record['id']
            tag_match = any(term in tag.casefold() for tag in record['tags']['values'] if isinstance(tag, str)
                            for term in terms)
            like_example = kind == 'attraction' and (rec_id in exemplar_ids or
                                                      matches_tags(record['tags']['values'], exemplar_tags, ()))
            name_match=kind=='attraction' and theme_name_matches(record['name'],terms)
            return (rec_id not in exact_ids, rec_id not in matched_experiences, rec_id not in possible_ids,
                    not (tag_match or like_example or name_match))

        candidates[kind] = sorted(eligible, key=rank)[:limit]
        coverage[kind] = {'discoverable': len(rows), 'planning_catalog': usable, 'matched': len(eligible),
                          'returned': len(candidates[kind])}
    return candidates, coverage, experiences


def snapshot_for_trip(facts, spec, limit=12):
    if (not isinstance(spec, dict) or spec.get('schema_version') != 'tripspec_v1'
            or spec.get('ready') is not True or spec.get('state') != 'ready' or spec.get('issues')):
        raise ValueError('旅行事实需要一份已确认且无待确认项的 TripSpec')
    fields = ('field', 'value', 'strength', 'operator', 'scope', 'unit', 'source')
    items = spec.get('items')
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not all(f in item for f in fields)
        or any(not isinstance(item[key], str) for key in ('field', 'strength', 'operator', 'scope'))
        for item in items
    ):
        raise ValueError('TripSpec 缺少结构化需求项')
    revision = positive_integer(spec.get('request_revision'), '需求版本')
    optional = ('open_experience', 'primary', 'maximize_matches', 'area_term', 'preferred_start')
    requirements = [{'id': f'requirement-{i}', **{f: item[f] for f in (*fields, *optional) if f in item}}
                    for i, item in enumerate(items, 1)]
    core = {}
    for item in requirements:
        field, value = item['field'], item['value']
        if field in ('origin_city', 'destination_city', 'days', 'people', 'start_date'):
            if field in core and core[field] != value:
                raise ValueError(f'TripSpec 的 {field} 存在冲突')
            core[field] = value
    origin, city = core.get('origin_city'), core.get('destination_city')
    facts._city(origin)
    facts._city(city)
    days = positive_integer(core.get('days'), '天数')
    people = positive_integer(core.get('people'), '人数')
    start_date = core.get('start_date')
    start = None if relative_start(start_date) else day(start_date)
    start_date = start.isoformat() if start else None
    try:
        end_date = (start + timedelta(days=days - 1)).isoformat() if start else None
    except OverflowError as error:
        raise ValueError('出发日期与天数超出日期范围') from error
    limit = min(positive_integer(limit, '返回条数'), 100)
    unapplied = []
    modifiers = place_modifiers(items, spec.get('raw_text', ''))
    groundings = _ground_places(facts, city, start, days, requirements, modifiers)
    filters = _filters(requirements, unapplied)
    candidates, coverage, experiences = _candidate_lists(facts, city, requirements, groundings, filters, limit)
    intercity = _ticket_queries(facts, origin, city, start_date, end_date, requirements, limit, unapplied)
    result = {
        'schema_version': 'world_snapshot_v1', 'selection_policy': 'confirmed_trip_v1',
        'data': dict(facts.version), 'request_id': spec.get('request_id'), 'request_revision': revision,
        'origin': origin, 'destination': city, 'days': days, 'people': people,
        'requested_start_date': start_date, 'requested_return_date': end_date,
        'requirements': requirements, 'place_groundings': groundings, 'candidates': candidates,
        'coverage': coverage, 'intercity': intercity, 'unapplied_fact_filters': unapplied,
        'verification_scope': {'kind': 'snapshot_reference', 'current_inventory': 'unknown',
                               'actual_room_type_and_occupancy_verified': False,
                               'room_type_policy': 'booking_note'},
    }
    if modifiers:
        result['place_modifiers'] = modifiers
    if reference_route(origin, city):
        result['verification_scope']['historical_city_reference'] = True
    if experiences:
        result['experience_matches'] = experiences
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return {**result, 'snapshot_id': sha256(encoded.encode('utf-8')).hexdigest()}
