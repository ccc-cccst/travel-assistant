"""Whole-trip day layouts grounded in one WorldSnapshot."""

from collections import defaultdict
from hashlib import sha256
import json
import math

from .tripspec import request_deepseek_json
from .trip_world import (POSITIVE, excluded_visit_ids, exemplar_evidence, matches_tags, preferred_tag_terms,
                         requested_experience_match)


VERSION = 'trip_sketch_v1'
PLACE_LISTS = {'core_ids': 'attraction', 'alternative_ids': 'attraction',
               'restaurant_ids': 'restaurant', 'lodging_ids': 'accommodation'}
LAYOUT_GOALS = {'appointment', 'attraction', 'restaurant', 'hotel', 'near_anchor',
                'cuisine', 'experience_theme', 'experience_example', 'hotel_feature', 'pace'}
NEGATIVE = {'exclude', 'avoid', 'no_preference', 'unspecified'}
MODEL_PROMPT = """根据 JSON 事实快照，为整趟旅行提出最多两套有实际差异的每日地点构想。
仅从 places 给出的 ID 中选择。每天的地点无游览顺序。相互接近且体验互补的景点优先搭配。
点名地点若状态为 found，优先纳入。ambiguous 等状态只可试排相关地点，保留歧义。
固定预约必须在指定日的 core_ids，其他日期不能移动预约。每个核心景点通常只出现一天。
第一天及返程日可能较短，可留空。餐厅和住宿为同区域建议，末日住宿列表留空。
若偏好为充实，优先安排同区域的两个核心景点，或保留一个适合长时间体验的地点。首末日按实际可用窗口弹性处理。
所有日期从 1 到 days，每个日期恰好一次。各列表为地点 ID，允许空列表。
每日最多 3 个核心景点（预约更多时保留全部预约）、2 个备选景点、2 家餐厅、2 家住宿。
不要输出班次、价格、路线、时刻或可行性承诺。
只输出一个 JSON 对象，顶层唯一字段是 layouts。每一套构想直接是逐日对象的数组。
形状示例展示两套单日构想：{"layouts":[[{"day":1,"core_ids":[],"alternative_ids":[],"restaurant_ids":[],"lodging_ids":[]}],[{"day":1,"core_ids":[],"alternative_ids":[],"restaurant_ids":[],"lodging_ids":[]}]]}。
每套按实际天数填充，整趟至少有一个核心景点。"""


def _records(snapshot):
    records = {}
    for group in snapshot['candidates'].values():
        for row in group:
            records[row['id']] = row
    for grounding in snapshot['place_groundings']:
        for row in grounding['records']:
            if row['kind'] == 'attraction':
                records.setdefault(row['id'], row)
    for goal in snapshot.get('experience_matches', {}).values():
        for row in goal['records']:
            records.setdefault(row['id'], row)
    excluded = {place['id'] for place in excluded_visit_ids(
        snapshot['requirements'], snapshot['place_groundings']).values()}
    return {key: row for key, row in records.items() if row['routable_in_snapshot'] and key not in excluded}


def _appointments(snapshot, records):
    locks, missing = [], []
    resolved = set()
    for g in snapshot['place_groundings']:
        if 'visit' not in g:
            continue
        if g['status'] == 'found' and g['records'][0]['id'] in records:
            locks.append({'requirement_id': g['requirement_id'], 'id': g['records'][0]['id'],
                          **g['visit']})
            resolved.add(g['requirement_id'])
    for item in snapshot['requirements']:
        if item['field'] == 'appointment' and item['id'] not in resolved:
            missing.append({'code': 'appointment_location_unresolved', 'requirement_id': item['id']})
    return locks, missing


def _preferences(snapshot, records):
    items = {item['id']: item for item in snapshot['requirements'] if item['operator'] not in NEGATIVE}
    groups = defaultdict(list)
    exact, possible = [], []
    for g in snapshot['place_groundings']:
        item = items.get(g['requirement_id'])
        if not item or item['field'] not in ('attraction', 'restaurant', 'hotel'):
            continue
        options = [p['id'] for p in g['records'] if p['id'] in records]
        if options:
            groups[item['id']].append(options)
            ranked = exact if g['status'] == 'found' else possible
            ranked.extend(options[:1])
    return list(dict.fromkeys(exact)), list(dict.fromkeys(possible)), groups


def _distance(origin, target):
    if not origin or not target:
        return math.inf
    return (origin['lat'] - target['lat']) ** 2 + (origin['lon'] - target['lon']) ** 2


def _local_layouts(snapshot, records, locks):
    exact, possible, groups = _preferences(snapshot, records)
    named_ids = set(exact + possible)
    related = {ref for group in groups.values() for alternatives in group for ref in alternatives}
    fixed = defaultdict(list)
    for lock in locks:
        if lock['id'] not in fixed[lock['day']]:
            fixed[lock['day']].append(lock['id'])
    locked_ids = {lock['id'] for lock in locks}
    normal = [ref for ref, row in records.items() if row['kind'] == 'attraction'
              and ref not in related and ref not in locked_ids]
    seeds = [ref for ref in exact + possible if records[ref]['kind'] == 'attraction' and ref not in locked_ids]
    for item in sorted(snapshot['requirements'], key=lambda req: not req.get('primary', False)):
        if item.get('open_experience'):
            matches = snapshot.get('experience_matches', {}).get(item['id'], {}).get('records', [])
            seeds.extend(row['id'] for row in matches[:2] if row['id'] not in locked_ids)
    seeds = list(dict.fromkeys(seeds))
    normal = [ref for ref in normal if ref not in seeds]
    others = [ref for ref in records if records[ref]['kind'] == 'attraction' and ref not in locked_ids]
    if not normal and not seeds:
        seeds = others[:1]
    relaxed = any(i['field'] == 'pace' and i['value'] == '舒缓' and i['operator'] not in NEGATIVE
                  for i in snapshot['requirements'])

    def closest(ids, anchor, count, prefer_named=False):
        origin = records[anchor]['location'] if anchor else None
        return sorted(ids, key=lambda i: (prefer_named and i not in named_ids,
                                          _distance(origin, records[i]['location'])))[:count]

    restaurants = [ref for ref, row in records.items() if row['kind'] == 'restaurant']
    lodging = [ref for ref, row in records.items() if row['kind'] == 'accommodation']
    layouts = []
    for offset in range(2 if len(normal) > 1 else 1):
        ordinary = normal[offset:] + normal[:offset]
        if relaxed and offset and ordinary:
            ordinary = closest(ordinary, ordinary[0], len(ordinary))
        delayed_seed_day = next((d for d in range(1, snapshot['days']) if d not in fixed), None) if (
            offset and snapshot['days'] - len(fixed) > len(seeds) and seeds
        ) else None
        used = set(locked_ids)
        days = []
        for day in range(1, snapshot['days'] + 1):
            priority = ordinary + seeds if day == delayed_seed_day else seeds + ordinary
            available = [i for i in priority if i not in used]
            core = list(fixed.get(day, ()))
            if not core and available:
                core.append(available[0])
            anchor = core[0] if core else None
            stay = records[anchor]['recommended_stay_hours'] if anchor else None
            if (anchor and anchor not in named_ids and len(core) == 1 and not relaxed
                    and day < snapshot['days'] and (not stay or stay[0] < 3)):
                nearby = [i for i in ordinary if i not in used and i not in core]
                core.extend(closest(nearby, anchor, 1))
            used.update(core)
            options = [i for i in ordinary if i not in used]
            days.append({'day': day, 'core_ids': core, 'alternative_ids': closest(options, anchor, 2),
                         'restaurant_ids': closest(restaurants, anchor, 2, prefer_named=True),
                         'lodging_ids': closest(lodging, anchor, 2, prefer_named=True)
                         if day < snapshot['days'] else []})
        if any(entry['core_ids'] for entry in days):
            layouts.append(days)
    return layouts


def _local_experience_layout(snapshot, records, locks):
    """Try a stay near a requested attraction theme and cuisine."""
    matched = {}
    for field, kind in (('experience_theme', 'attraction'), ('cuisine', 'restaurant')):
        values = []
        for item in snapshot['requirements']:
            if item['field'] != field or item['strength'] != 'soft' or item['operator'] not in POSITIVE:
                continue
            options = item['value'] if isinstance(item['value'], list) else [item['value']]
            values.extend(v.strip() for v in options if isinstance(v, str) and v.strip())
        if not values:
            return None
        terms = preferred_tag_terms(field, values, snapshot['destination'])
        matched[kind] = [key for key, row in records.items() if row['kind'] == kind
                         and row['location'] and matches_tags(row['tags']['values'], *terms)]
        if not matched[kind]:
            return None

    fixed = defaultdict(list)
    for lock in locks:
        if lock['id'] not in fixed[lock['day']]:
            fixed[lock['day']].append(lock['id'])
    free_days = [d for d in range(1, snapshot['days'] + 1) if d not in fixed]
    if not free_days:
        return None
    anchor_day = min(free_days, key=lambda d: abs(d - (snapshot['days'] + 1) / 2))
    reserved = {ref for ids in fixed.values() for ref in ids}
    attractions = [ref for ref in matched['attraction'] if ref not in reserved]
    if not attractions:
        return None
    exact, possible, _ = _preferences(snapshot, records)
    priorities = set(exact + possible)
    hotels = [ref for ref, row in records.items() if row['kind'] == 'accommodation' and row['location']]
    food = matched['restaurant']

    def distance(first, second):
        return _distance(records[first]['location'], records[second]['location'])

    anchor = min(attractions, key=lambda ref: (ref not in priorities,
                 min(distance(ref, meal) for meal in food) + min((distance(ref, h) for h in hotels), default=0)))
    lodging = sorted(hotels, key=lambda ref: (ref not in priorities, distance(ref, anchor)))[:2]
    restaurants = sorted(food, key=lambda ref: (ref not in priorities, distance(ref, anchor)))[:2]
    nearby = [ref for ref, row in records.items() if row['kind'] == 'attraction'
              and row['location'] and ref not in reserved and ref != anchor]
    nearby.sort(key=lambda ref: (ref not in priorities, distance(ref, anchor)))
    # A full day can pair the anchor with a second nearby stop. Boundary
    # days keep more room for arrival and departure.
    anchor_core = [anchor]
    if 1 < anchor_day < snapshot['days'] and nearby and distance(anchor, nearby[0]) <= 0.02 ** 2:
        anchor_core.append(nearby.pop(0))
    layout = []
    for day in range(1, snapshot['days'] + 1):
        core = fixed.get(day) or (anchor_core if day == anchor_day else nearby[:1])
        if core and core[0] in nearby:
            nearby.remove(core[0])
        layout.append({'day': day, 'core_ids': core, 'alternative_ids': nearby[:2],
                       'restaurant_ids': restaurants if day == anchor_day else [],
                       'lodging_ids': lodging if day < snapshot['days'] else []})
    return layout


def _named_meal_layout(snapshot, records, locks):
    """Place a named meal on an interior day with nearby activities and lodging."""
    if locks or snapshot['days'] < 2:
        return None
    if any(i['field'] in ('attraction', 'hotel') and i['operator'] in POSITIVE
           for i in snapshot['requirements']):
        return None
    meals = {i['id'] for i in snapshot['requirements'] if i['field'] == 'restaurant'
             and i['strength'] == 'soft' and i['operator'] in ('select', 'prefer', 'include', 'prioritize')}
    exact = list(dict.fromkeys(r['id'] for g in snapshot['place_groundings']
                              if g['requirement_id'] in meals and g['status'] == 'found'
                              for r in g['records'] if r['id'] in records))
    if len(exact) != 1:
        return None
    meal, = exact

    def nearby(kind):
        return sorted((ref for ref, row in records.items() if row['kind'] == kind),
                      key=lambda ref: _distance(records[meal]['location'], records[ref]['location']))

    attractions, hotels = nearby('attraction'), nearby('accommodation')[:2]
    if not attractions or not hotels:
        return None
    meal_day = (snapshot['days'] + 1) // 2
    layout = []
    for day in range(1, snapshot['days'] + 1):
        count = 2 if day == meal_day else 1
        core, attractions = attractions[:count], attractions[count:]
        layout.append({'day': day, 'core_ids': core, 'alternative_ids': attractions[:2],
                       'restaurant_ids': [meal] if day == meal_day else [],
                       'lodging_ids': hotels if day < snapshot['days'] else []})
    return layout


def _regional_layout(snapshot, records, locks):
    """Use a shared hotel area and group nearby visits on the same day."""
    if snapshot['days'] < 2:
        return None
    named, possible, _ = _preferences(snapshot, records)
    attractions = [ref for ref, r in records.items() if r['kind'] == 'attraction']
    hotels = [ref for ref, r in records.items() if r['kind'] == 'accommodation']
    if not attractions or not hotels:
        return None

    def distance(a, b):
        return _distance(records[a]['location'], records[b]['location'])

    def hotel_cost(h):
        return records[h]['price']['value']

    def compactness(h):
        anchors=set(named+possible+[a['id'] for a in locks])
        if anchors:
            return sum(distance(h,a) for a in anchors)
        distances = sorted(distance(h, a) for a in attractions)
        return sum(distances[:min(len(distances), snapshot['days'] + 1)])

    center = min(hotels, key=lambda h: (compactness(h), hotel_cost(h)))
    nearby_hotel = min(hotels, key=lambda h: (distance(h, center) > .04 ** 2, hotel_cost(h)))
    lodging = list(dict.fromkeys((center, nearby_hotel)))
    remaining = sorted(attractions, key=lambda a: distance(center, a))
    fixed=defaultdict(list)
    for lock in locks:
        if lock['id'] not in fixed[lock['day']]:
            fixed[lock['day']].append(lock['id'])
    fixed_ids={r for group in fixed.values() for r in group}
    remaining=[r for r in remaining if r not in fixed_ids]
    priority=[r for r in dict.fromkeys(named+possible) if r in remaining]
    meal_ids=[r for r in named if records[r]['kind']=='restaurant']
    named_hotels=[r for r in named if records[r]['kind']=='accommodation']
    if named_hotels:
        lodging=named_hotels[:2]
    layout = []
    for day in range(1, snapshot['days'] + 1):
        edge = day in (1, snapshot['days'])
        core=list(fixed.get(day,()))
        anchor = core[0] if core else priority[0] if priority else remaining[0] if remaining else center
        if not core and anchor!=center:
            core.append(anchor)
            remaining.remove(anchor)
        priority=[r for r in priority if r in remaining]
        neighbors = sorted(remaining, key=lambda a: distance(anchor, a))
        if neighbors and len(core)<3 and not edge and distance(anchor, neighbors[0]) <= .05 ** 2:
            core.append(neighbors.pop(0))
            remaining.remove(core[-1])
            priority=[r for r in priority if r in remaining]
        layout.append({'day': day, 'core_ids': core, 'alternative_ids': neighbors[:2],
                       'restaurant_ids': [], 'lodging_ids': lodging if day < snapshot['days'] else []})
    for meal in meal_ids:
        available=[d for d in layout if d['core_ids'] and len(d['restaurant_ids'])<2]
        if available:
            closest=min(available,key=lambda d:min(distance(meal,p) for p in d['core_ids']))
            closest['restaurant_ids'].append(meal)
    return layout


def _validate_layout(entries, snapshot, records, locks):
    if not isinstance(entries, list) or len(entries) != snapshot['days']:
        return None, 'invalid_day_count'
    seen_days, seen_core = set(), set()
    locked_days = defaultdict(set)
    for lock in locks:
        locked_days[lock['id']].add(lock['day'])
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'day', *PLACE_LISTS}:
            return None, 'invalid_day_fields'
        day = entry['day']
        if type(day) is not int or not 1 <= day <= snapshot['days'] or day in seen_days:
            return None, 'invalid_day_number'
        seen_days.add(day)
        required = {ref for ref, days in locked_days.items() if day in days}
        for field, kind in PLACE_LISTS.items():
            values = entry[field]
            cap = max(3, len(required)) if field == 'core_ids' else 2
            if not isinstance(values, list) or len(values) > cap or any(not isinstance(ref, str) for ref in values):
                return None, 'invalid_place_list'
            if any(ref not in records or records[ref]['kind'] != kind for ref in values):
                return None, 'unknown_or_wrong_place_type'
            if len(set(values)) != len(values):
                return None, 'duplicate_place_in_list'
        cores, alternatives = set(entry['core_ids']), set(entry['alternative_ids'])
        if not required.issubset(cores) or any(ref in locked_days and day not in locked_days[ref] for ref in cores):
            return None, 'appointment_missing_or_moved'
        if alternatives.intersection(cores | locked_days.keys()):
            return None, 'invalid_alternative'
        if (seen_core & cores) - required:
            return None, 'duplicate_core_across_days'
        if day == snapshot['days'] and entry['lodging_ids']:
            return None, 'lodging_on_final_day'
        seen_core.update(cores)
    if not seen_core:
        return None, 'empty_trip_layout'
    return sorted(entries, key=lambda e: e['day']), None


def _goal_evidence(snapshot, layout, records):
    placements = defaultdict(list)
    for entry in layout:
        for field in ('core_ids', 'restaurant_ids', 'lodging_ids'):
            for ref in entry[field]:
                placements[ref].append(entry['day'])
    by_requirement = defaultdict(list)
    for entry in snapshot['place_groundings']:
        by_requirement[entry['requirement_id']].append(entry)
    evidence = []
    for item in snapshot['requirements']:
        field, value = item['field'], item['value']
        if field not in LAYOUT_GOALS or item['operator'] in NEGATIVE:
            continue
        groundings = by_requirement[item['id']]
        hits = {}
        status = 'deferred_to_planning'
        if item.get('open_experience'):
            hits = {ref: days for ref, days in placements.items() if records[ref]['kind'] == 'attraction'
                    and requested_experience_match(item, records[ref]['name'], records[ref]['tags']['values'],
                                                   snapshot['destination'])}
            known = snapshot.get('experience_matches', {}).get(item['id'], {}).get('matched_count', 0)
            status = 'proposed' if hits else 'not_proposed' if known else 'missing_reference'
        elif field == 'experience_example' and item['operator'] == 'example_of':
            ids, tags = exemplar_evidence(groundings)
            hits = {ref: days for ref, days in placements.items() if ref in ids or
                    (records[ref]['kind'] == 'attraction' and matches_tags(records[ref]['tags']['values'], tags, ()))}
            status = 'proposed' if hits else 'not_proposed' if ids else 'missing_reference'
        elif groundings:
            exact = {p['id'] for g in groundings if g['status'] == 'found' for p in g['records']}
            uncertain = {p['id'] for g in groundings if g['status'] in ('ambiguous', 'unconfirmed_match')
                         for p in g['records']}
            hits = {ref: days for ref, days in placements.items() if ref in exact or ref in uncertain}
            if uncertain:
                status = 'ambiguous_reference'
            elif not exact:
                status = 'missing_reference'
            else:
                status = 'proposed' if hits else 'not_proposed'
        elif field in ('cuisine', 'experience_theme', 'hotel_feature'):
            kinds = {'cuisine': 'restaurant', 'experience_theme': 'attraction', 'hotel_feature': 'accommodation'}
            values = value if isinstance(value, list) else [value]
            terms = preferred_tag_terms(field, [v.strip() for v in values if isinstance(v, str) and v.strip()],
                                        snapshot['destination'])
            hits = {ref: days for ref, days in placements.items() if records[ref]['kind'] == kinds[field]
                    and matches_tags(records[ref]['tags']['values'], *terms)}
            status = 'proposed' if hits else 'not_proposed'
        evidence.append({'requirement_id': item['id'], 'status': status,
                         'places': [{'id': ref, 'days': days} for ref, days in hits.items()]})
    return evidence


def _model_payload(snapshot, records, locks):
    places = []
    ordered = sorted(records.values(), key=lambda row: row['kind'])
    for row in ordered:
        pos = row['location']
        places.append([row['id'], row['name'], row['tags']['values'],
                       [round(pos['lat'], 3), round(pos['lon'], 3)] if pos else None])
    return {'destination': snapshot['destination'], 'days': snapshot['days'], 'people': snapshot['people'],
            'requirements': [{k: i[k] for k in ('id', 'field', 'strength', 'operator', 'scope', 'value', 'unit',
                                                'area_term', 'preferred_start') if k in i}
                             for i in snapshot['requirements'] if i['field'] in LAYOUT_GOALS
                             or i['field'] in ('budget', 'meal_budget', 'intercity_mode')],
            'named': [{'requirement_id': g['requirement_id'], 'status': g['status'],
                       'ids': [p['id'] for p in g['records'] if p['id'] in records]} for g in snapshot['place_groundings']],
            'appointments': [[a['day'], a['id']] for a in locks],
            'place_columns': ['id', 'name', 'tags', 'lat_lon'], 'places': places}


def propose_trip_sketches(snapshot, *, use_model=False, provider=request_deepseek_json, timeout=12):
    """Generate model proposals and deterministic alternatives from a saved snapshot."""
    if (not isinstance(snapshot, dict) or snapshot.get('schema_version') != 'world_snapshot_v1'
            or snapshot.get('selection_policy') != 'confirmed_trip_v1'
            or not isinstance(snapshot.get('snapshot_id'), str)
            or type(snapshot.get('days')) is not int or snapshot['days'] < 1):
        raise ValueError('体验构想需要由确认版 TripSpec 生成的 WorldSnapshot')
    records = _records(snapshot)
    locks, issues = _appointments(snapshot, records)
    model = {'attempts': 0, 'elapsed_seconds': 0, 'usage': {}, 'error': None, 'name': None,
             'draft': None, 'rejected': []}
    layouts = [('program', days) for days in _local_layouts(snapshot, records, locks)]
    nearby = _local_experience_layout(snapshot, records, locks)
    if nearby:
        days, reason = _validate_layout(nearby, snapshot, records, locks)
        if not reason:
            layouts.append(('nearby_experiences', days))
    if named_meal := _named_meal_layout(snapshot, records, locks):
        days, reason = _validate_layout(named_meal, snapshot, records, locks)
        if not reason:
            layouts.append(('nearby_named_meal', days))
    if region := _regional_layout(snapshot, records, locks):
        days, reason = _validate_layout(region, snapshot, records, locks)
        if not reason:
            layouts.append(('nearby_regions', days))
    if use_model and layouts and not issues:
        reply = provider(MODEL_PROMPT, _model_payload(snapshot, records, locks), max_tokens=1536, timeout=timeout)
        model.update(attempts=reply.attempts, elapsed_seconds=reply.elapsed_seconds, usage=reply.usage,
                     error=reply.error, name=reply.model, draft=reply.draft)
        if reply.invalid_content is not None:
            model['invalid_content'] = reply.invalid_content
        proposals = reply.draft.get('layouts') if isinstance(reply.draft, dict) and set(reply.draft) == {'layouts'} else None
        if not reply.error and (not isinstance(proposals, list) or not proposals):
            model['error'] = 'invalid_or_empty_sketch_list'
        if not model['error']:
            for idx, proposal in enumerate(proposals[:2], 1):
                days, reason = _validate_layout(proposal, snapshot, records, locks)
                if reason:
                    model['rejected'].append({'proposal': idx, 'reason': reason})
                else:
                    layouts.append(('model', days))
    sketches = []
    regional_alternative = None
    normal_region = (snapshot['days'] >= 3 and not locks and not any(_preferences(snapshot,records)[:2])
                     and any(i['strength']=='soft' and ((i['field']=='transport_accessibility' and i['value'] is True)
                             or (i['field']=='transit_time' and i['operator']=='minimize'))
                             for i in snapshot['requirements']))
    seen = set()
    for source, days in layouts:
        signature = tuple(tuple(sorted(d['core_ids'])) for d in days)
        if source in ('nearby_named_meal', 'nearby_regions'):
            signature = (signature, tuple((tuple(d['restaurant_ids']), tuple(d['lodging_ids'])) for d in days))
        if signature in seen:
            if source == 'model':
                model['rejected'].append({'reason': 'duplicate_core_layout'})
            continue
        seen.add(signature)
        key = json.dumps([snapshot['snapshot_id'], signature], separators=(',', ':'))
        basis = {'program': 'named_goals_and_geographic_proximity', 'model': 'model_proposal',
                 'nearby_experiences': 'theme_cuisine_and_lodging_proximity',
                 'nearby_named_meal': 'named_restaurant_and_nearby_lodging',
                 'nearby_regions': 'daily_visit_clusters_and_shared_lodging'}
        sketches.append({'id': sha256(key.encode()).hexdigest()[:24], 'source': source, 'days': days,
                         'basis': basis[source],
                         'goal_evidence': _goal_evidence(snapshot, days, records)})
        if source=='nearby_regions' and not normal_region:
            regional_alternative=sketches.pop()
    return {'schema_version': VERSION, 'snapshot_id': snapshot['snapshot_id'],
            'request_id': snapshot['request_id'], 'request_revision': snapshot['request_revision'],
            'state': 'needs_confirmation' if issues else 'ready_for_trial' if sketches else 'no_activity_candidates',
            'fixed_appointments': locks, 'issues': issues, 'sketches': sketches, 'model': model,
            'regional_alternative':regional_alternative}
