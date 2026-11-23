"""Shared adapters for the existing bounded constructor and snapshot verifier."""
import inspect
from pathlib import Path
import sys

from .trip_world import excluded_visit_ids, hotel_feature_options, hotel_feature_tag
from .open_requirements import place_modifiers
from .tripspec import relative_start
from .world_facts import clock_minutes, day, numeric

# The distribution bundles the constructor. Development checkouts share the baseline.
SHARED_PLANNER = Path(__file__).resolve().parents[3] / 'ShortTrip'
if not (Path(__file__).resolve().parents[1] / 'planning').is_dir() and str(SHARED_PLANNER) not in sys.path:
    sys.path.append(str(SHARED_PLANNER))

from chinatravel.planning.engine import BUDGET_EXCLUSIONS, clock, total_cost
from chinatravel.planning.preferences import normalize_profile
from chinatravel.planning.snapshot_constraints import RAIL_CLASSES
from chinatravel.planning.validation import validate_plan


LOCAL_MODES = {'地铁': 'metro', '出租车': 'taxi', '打车': 'taxi', '步行': 'walk',
               'metro': 'metro', 'taxi': 'taxi', 'walk': 'walk'}
DEFAULT_LOCAL_MODES = ('metro', 'taxi', 'walk')
CONTEXT_FIELDS = {'party_context', 'trip_context', 'meeting_area', 'experience_theme'}
ARRIVAL_STATION_ALIASES = {'大兴机场': '北京大兴国际机场', '大兴国际机场': '北京大兴国际机场'}
ARRIVAL_PERIODS = {'早上': ('06:00', '12:00'), '上午': ('06:00', '12:00')}
# a suggested evening start, not evidence for any dated departure
EVENING_EXPERIENCES = {'广州': {'珠江夜游': '18:00'}}


def local_mode_options(item):
    """Return the permitted leg modes for one hard, trip-wide local travel rule."""
    if item['scope'] != 'local_travel':
        return None
    value, op = item['value'], item['operator']
    values = value if isinstance(value, list) else [value]
    if not values or any(not isinstance(v, str) or v not in LOCAL_MODES for v in values):
        return None
    modes = {LOCAL_MODES[v] for v in values}
    if op == 'exclude':
        return set(DEFAULT_LOCAL_MODES) - modes
    if op in ('select', 'allowed_set', 'any_of', 'eq'):
        # V20 metro routes include walking to and from stations. Walking also
        # covers nearby endpoints served by the same station.
        return modes | {'walk'} if 'metro' in modes else modes
    return None


def constructor_input(spec, *, snapshot=None):
    core = {a['field']: a['value'] for a in spec['items'] if a['field'] in
            ('origin_city', 'destination_city', 'people', 'days')}
    request = dict(start_city=core['origin_city'], target_city=core['destination_city'],
                   days=core['days'], people_number=core['people'], appointments=[], reference_lodging=True)
    if core['destination_city'] in EVENING_EXPERIENCES:
        request['experience_earliest'] = EVENING_EXPERIENCES[core['destination_city']]
    profile = {}
    unsupported = []
    local_modes = None
    starts = [i['value'] for i in spec['items'] if i['field'] == 'start_date']
    modifiers = place_modifiers(spec['items'], spec.get('raw_text', ''))
    for index, item in enumerate(spec['items'], 1):
        field, value, op = item['field'], item['value'], item['operator']
        if field == 'room_type':
            continue
        if (field=='attraction' and item['strength'] in ('hard','fact') and
                op in ('select','eq','require','include') and isinstance(value,str) and value.strip()):
            request.setdefault('required_visit_queries',[]).append(value)
            continue
        if field == 'attraction' and item['strength'] == 'hard' and op == 'exclude' and isinstance(value, str) and value.strip():
            request.setdefault('excluded_visit_queries', []).append(value)
            continue
        if item['strength'] == 'soft':
            if field == 'pace' and value in ('舒缓', '充实'):
                profile['pace'] = 'relaxed' if value == '舒缓' else 'full'
            elif field == 'experience_theme' and value == '博物馆':
                profile.setdefault('interest_types', []).append('博物馆/纪念馆')
            elif field == 'cuisine' and isinstance(value, str) and op == 'prefer':
                profile.setdefault('restaurant_types', []).append(value)
            continue
        if field in core:
            continue
        if item['strength'] == 'fact' and (field in CONTEXT_FIELDS or (
                field == 'start_date' and (relative_start(value) or day(value)))):
            continue
        if field == 'cuisine' and op == 'no_preference' and item['strength'] == 'fact' and value is True:
            continue
        if item['strength'] == 'fact' and isinstance(value, str) and value.strip():
            if field == 'near_anchor' and f'requirement-{index}' in modifiers:
                continue
            if field == 'dish' and op == 'accept' and item['scope'] == 'food':
                continue
            if field == 'experience_example' and op == 'example_of' and item['scope'] == 'total_trip':
                continue
            if field == 'date_scope' and op == 'eq' and item['scope'] == 'total_trip' and value == '周末':
                if core['days'] <= 2 and all(not relative_start(s) and day(s) is not None and
                        5 <= day(s).weekday() <= 7 - core['days'] for s in starts):
                    continue
        if field == 'appointment' and item['strength'] == 'hard':
            request['appointments'].append(dict(place=value['name'], day=value['day'], confirmed=True,
                start_time=value['start_time'], end_time=clock(clock_minutes(value['start_time']) + value['duration_minutes'])))
        elif field == 'budget' and op == 'lte' and item['unit'] in ('CNY/group','CNY/person'):
            cap = value * (core['people'] if item['unit'] == 'CNY/person' else 1)
            if item['scope'] == 'total_trip':
                request['budget'] = min(cap, request.get('budget', cap))
            elif item['scope'] in BUDGET_EXCLUSIONS:
                limits = request.setdefault('scoped_budgets', {})
                limits[item['scope']] = min(cap, limits.get(item['scope'], cap))
            else:
                unsupported.append({**item, 'planning_note': '这笔预算排除了部分费用，请确认具体包含哪些支出。'})
        elif field == 'intercity_mode' and item['scope'] in (
                'round_trip','intercity_trip','outbound','return') and op in ('select','allowed_set','any_of','eq'):
            modes = value if isinstance(value, list) else [value]
            if modes and all(isinstance(m, str) and m in ('火车', '飞机', *RAIL_CLASSES) for m in modes):
                scope = item['scope']
                directions = (scope,) if scope in ('outbound','return') else ('outbound','return')
                for direction in directions:
                    request.setdefault('ticket_restrictions', {}).setdefault(direction, []).append(modes)
            else:
                unsupported.append(item)
        elif field == 'local_mode' and item['strength'] == 'hard':
            options = local_mode_options(item)
            if options is None:
                unsupported.append({**item, 'planning_note': '本地路线可核验地铁、出租车和步行的全程限定。请确认允许的方式。'})
            else:
                local_modes = options if local_modes is None else local_modes & options
        elif field == 'arrival_station' and item['scope'] == 'outbound' and op in ('eq', 'select') and (
                isinstance(value, str) and value.strip()):
            request.setdefault('outbound_arrival_stations', []).append(ARRIVAL_STATION_ALIASES.get(value, value))
        elif field == 'arrival_time' and item['scope'] == 'outbound' and op in ('eq', 'approx') and (
                isinstance(value, str) and value in ARRIVAL_PERIODS):
            request['outbound_arrival_period'] = ARRIVAL_PERIODS[value]
        elif field == 'room_count' and op == 'eq':
            request['rooms'] = value
        elif field == 'nights' and op == 'eq' and item['unit'] == 'night':
            request['lodging_nights'] = value
        elif field == 'lodging_in_plan' and value is True and op in ('include', 'require', 'eq'):
            request['requires_lodging'] = True
        elif field == 'hotel' and item['strength'] == 'hard' and item['scope'] in ('lodging', 'total_trip') and (
                op in ('select', 'eq', 'require', 'include') and isinstance(value, str) and value.strip()):
            request.setdefault('required_hotel_queries', []).append(value)
        elif field == 'lodging_budget' and op == 'lte' and item['scope'] == 'each_night' and (
                item['unit'] in ('CNY/person/night', 'CNY/group/night', 'CNY/room/night') and numeric(value) is not None):
            amount = numeric(value)
            if item['unit'] == 'CNY/room/night':
                cap_key = 'hotel_price_limit'
            else:
                cap_key = 'hotel_party_price_limit'
                if item['unit'] == 'CNY/person/night':
                    amount *= core['people']
            request[cap_key] = min(amount, request.get(cap_key, amount))
        elif field == 'hotel_feature' and op in ('require','include','eq') and isinstance(value, str):
            request.setdefault('required_hotel_features', []).append(hotel_feature_tag(value))
            request.setdefault('hotel_feature_options', {})[hotel_feature_tag(value)] = hotel_feature_options(value)
        elif field == 'meal_budget' and op == 'lte' and item['unit'] == 'CNY/person/meal':
            request['food_price_limit'] = value
        else:
            unsupported.append(item)
    if local_modes is not None:
        request['local_modes'] = tuple(m for m in DEFAULT_LOCAL_MODES if m in local_modes)
    if request.get('required_hotel_queries') and snapshot is not None:
        pins = {}
        for item in snapshot['requirements']:
            if item['field'] != 'hotel' or item['strength'] != 'hard':
                continue
            matches = [p for g in snapshot['place_groundings'] if g['requirement_id'] == item['id']
                       and g['status'] == 'found' for p in g['records']
                       if p['planning_catalog'] and p['routable_in_snapshot']]
            if len(matches) == 1:
                pins[item['id']] = {'id': matches[0]['id'], 'name': matches[0]['name']}
        request['required_hotel_ids'] = pins
        request['required_hotel_names'] = tuple(pin['name'] for pin in pins.values())
    if request.get('required_visit_queries') and snapshot is not None:
        pins={}
        for item in snapshot['requirements']:
            if (item['field'] != 'attraction' or item['strength'] not in ('hard', 'fact')
                    or item['operator'] not in ('select', 'eq', 'require', 'include')):
                continue
            rows=[p for g in snapshot['place_groundings'] if g['requirement_id']==item['id'] and g['status']=='found'
                  for p in g['records'] if p['planning_catalog'] and p['routable_in_snapshot']]
            if len(rows)==1:
                pins[item['id']]={'name':rows[0]['name'],'id':rows[0]['id']}
        request['required_visit_ids']=pins
        request['must_attractions']=[p['name'] for p in pins.values()]
    if snapshot is not None:
        if request.get('excluded_visit_queries'):
            pins = excluded_visit_ids(snapshot['requirements'], snapshot['place_groundings'])
            request['excluded_visit_ids'] = {key: row['id'] for key, row in pins.items()}
            request['excluded_attractions'] = [row['name'] for row in pins.values()]
        wanted = {item['id'] for item in snapshot['requirements'] if item['field'] == 'attraction'
                  and item['operator'] == 'prefer' and item['source'].get('type') == 'map_selection'}
        preferred = [r['name'] for g in snapshot['place_groundings']
                     if g['requirement_id'] in wanted and g['status'] == 'found'
                     for r in g['records'] if r['routable_in_snapshot']]
        if preferred:
            request['preferred_attractions'] = preferred
    return {**request, 'profile':normalize_profile(profile)}, unsupported


def bind_snapshot_routes(env, facts):
    # The snapshot's intercity lookup activates the pinned sandbox engine.
    from chinatravel.environment.tools.transportation.apis import Transportation
    if not Path(inspect.getfile(Transportation)).resolve().is_relative_to(facts.sandbox.root):
        raise RuntimeError('未能加载与 V20 核验器一致的路由实现。')
    if not isinstance(env.transportation, Transportation):
        env.transportation = Transportation(lang='zh')


def check_snapshot_reference(spec, snapshot, facts):
    fields = ('field', 'value', 'strength', 'operator', 'scope', 'unit')
    fields += ('open_experience', 'primary', 'maximize_matches')
    saved = [tuple(i.get(k) for k in fields) for i in snapshot['requirements']]
    submitted = [tuple(i.get(k) for k in fields) for i in spec['items']]
    if not spec.get('ready') or saved != submitted or snapshot['data'] != facts.version or (
        spec.get('request_id'), spec.get('request_revision')
    ) != (snapshot['request_id'], snapshot['request_revision']):
        raise ValueError('需求、快照与当前安装的数据版本必须一致')


def prepare_snapshot_routes(request, env, facts):
    # A cold sandbox query clears the previously imported snapshot tools.
    origin, destination = request['start_city'], request['target_city']
    facts.intercity(origin, destination, limit=1)
    facts.intercity(destination, origin, limit=1)
    bind_snapshot_routes(env, facts)
    from chinatravel.symbol_verification import commonsense_constraint
    commonsense_constraint._set_tool_lang('zh')


def audit(plan, request, env, facts):
    report = validate_plan(plan, request, env)
    if 'total_cost' not in report or any(
        item['code'] in ('same_day_time', 'transport_timeline') for item in report['issues']
    ):
        return report, {}, []
    visits, transit, walking = 0, 0, 0
    places = []
    ticket_facts = []
    hotel_facts = []
    scoped_budgets = request.get('scoped_budgets', {})
    seen = set()
    for d in plan['itinerary']:
        for a in d['activities']:
            for leg in a.get('transports', []):
                duration = clock_minutes(leg['end_time']) - clock_minutes(leg['start_time'])
                transit += duration
                if leg['mode'] == 'walk':
                    walking += duration
            kind = a['type']
            if kind in ('train','airplane'):
                reverse = len(ticket_facts) == 1
                start, end = request['start_city'], request['target_city']
                if reverse:
                    start, end = end, start
                service = a.get('TrainID' if kind == 'train' else 'FlightID')
                records = facts.intercity(start, end, service=service)['records']
                matched = [r for r in records if r['mode'] == kind and
                           (r['from'], r['to'], r['departure'], r['arrival'], r['fare']['value'])
                           == (a['start'], a['end'], a['start_time'], a['end_time'], a['price'])]
                ticket_facts.append({'direction':'return' if reverse else 'outbound','service':service,
                                     'records':matched})
                fare_required = request.get('budget') is not None or any(
                    kind not in BUDGET_EXCLUSIONS[scope] for scope in scoped_budgets)
                if not matched or (fare_required and not any(
                        r['fare']['usable_for_snapshot_budget'] for r in matched)):
                    report['issues'].append({'code':'ticket_price_evidence','service':service})
                continue
            category = {'lunch':'restaurant','dinner':'restaurant','breakfast':'restaurant'}.get(kind,kind)
            found = facts.place(request['target_city'], a['position'], category)
            if found['status'] != 'found':
                report['issues'].append({'code':'place_evidence','place':a['position'],'status':found['status']})
                continue
            p = found['records'][0]
            if category != 'accommodation':
                begin, end = clock_minutes(a['start_time']), clock_minutes(a['end_time'])
                if not any(lo <= begin < end <= hi for lo,hi in p['opening']['windows_minutes']):
                    report['issues'].append({'code':'opening_segments','place':p['id']})
                suggested = request.get('experience_earliest', {}).get(p['name']) if category == 'attraction' else None
                if suggested and begin < clock_minutes(suggested) and not any(
                        ap['day'] == d['day'] and ap['place'] == p['name']
                        and ap['start_time'] == a['start_time'] and ap['confirmed']
                        for ap in request.get('appointments', ())):
                    report['issues'].append({'code': 'experience_time', 'place': p['id'],
                                             'suggested_earliest': suggested})
            priced_requirement = request.get('budget') is not None or any(
                kind not in BUDGET_EXCLUSIONS[scope] for scope in scoped_budgets
            ) or (
                category == 'restaurant' and request.get('food_price_limit') is not None
                or category == 'accommodation' and any(
                    request.get(key) is not None for key in ('hotel_price_limit', 'hotel_party_price_limit'))
            )
            if priced_requirement and not p['price']['usable_for_snapshot_budget']:
                report['issues'].append({'code':'price_evidence','place':p['id']})
            if category == 'attraction':
                visits += end - begin
                seen.add(p['id'])
            if category == 'accommodation':
                hotel_facts.append({'day':d['day'],'id':p['id'],'name':p['name'],'source':p['source'],
                                    'features':p['tags'],'rooms':a['rooms'],
                                    'benchmark_bed_count':p['benchmark_bed_count'],
                                    'actual_room_type_and_occupancy_verified':False})
            places.append({'day':d['day'],'name':p['name'],'id':p['id'],'source':p['source'],'tags':p['tags']['values'],
                           'start_time':a['start_time'], 'end_time':a['end_time']})
    report['snapshot_evidence'] = {'tickets':ticket_facts,'overnight_stays':hotel_facts,'attraction_ids':sorted(seen)}
    for identifier in sorted(seen.intersection(request.get('excluded_visit_ids', {}).values())):
        report['issues'].append({'code': 'excluded_attraction_scheduled', 'place': identifier})
    if report.get('total_cost') is not None:
        for scope, limit in scoped_budgets.items():
            activities = (a for d in plan['itinerary'] for a in d['activities'])
            spent = round(total_cost(activities, exclude=BUDGET_EXCLUSIONS[scope]), 2)
            if spent > limit + .01:
                report['issues'].append({'code': 'scoped_budget', 'scope': scope,
                                         'observed': spent, 'limit': limit})
    report['passed'] = not report['issues'] and bool(seen)
    return report, {'total_cost_cny': report['total_cost'], 'city_transit_minutes':transit,
                    'access_walk_minutes':walking,'visit_minutes':visits,'distinct_attractions':len(seen)}, places
