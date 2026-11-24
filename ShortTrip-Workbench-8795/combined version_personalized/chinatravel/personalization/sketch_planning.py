"""Try saved day layouts and optimize the order of an independently checked day."""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from itertools import combinations
import json
import math
from time import perf_counter

from .city_scope import frozen_city_outcome
from .planning_compat import audit, check_snapshot_reference, constructor_input, prepare_snapshot_routes
from chinatravel.planning.engine import Constructor, SearchBudgetExpired, clock, minutes


INTERCITY = {'train', 'airplane'}
DAY_HINTS = {'attractions': 'core_ids', 'alternatives': 'alternative_ids',
             'restaurants': 'restaurant_ids', 'hotels': 'lodging_ids'}
LOCAL_LAYOUTS = {'nearby_experiences', 'nearby_named_meal'}


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:24]


def _sketch_input(sketch, names):
    hints = {entry['day']: {kind: [names[ref] for ref in entry[field]] for kind, field in DAY_HINTS.items()}
             for entry in sketch['days']}
    return {
        'day_preferences': hints,
        'preferred_attractions': list(dict.fromkeys(n for d in hints.values() for n in d['attractions'])),
        'preferred_hotels': list(dict.fromkeys(n for d in hints.values() for n in d['hotels'])),
    }


def _full_pace_sketch(sketch):
    """Offer a second stop using the day's existing nearby alternatives."""
    days = deepcopy(sketch['days'])
    occupied = {ref for day in days for ref in day['core_ids']}
    added = False
    # Interior days are usually less constrained by check-in and departure.
    for day in (*days[1:], days[0]):
        if len(day['core_ids']) != 1:
            continue
        spare = next((ref for ref in day['alternative_ids'] if ref not in occupied), None)
        if spare:
            day['core_ids'].append(spare)
            day['alternative_ids'].remove(spare)
            occupied.add(spare)
            added = True
    if not added:
        return None
    return {**sketch, 'id': _digest([sketch['id'], days]), 'days': days,
            'supplemented_for_pace': True}


def _plan_signature(plan):
    return tuple(tuple(sorted((a['type'], a.get('position', ''), a.get('TrainID', a.get('FlightID', '')),
                               a.get('start', ''), a.get('end', '')) for a in d['activities']))
                 for d in plan['itinerary'])


def _adoption(sketch, places):
    return [{'day': d['day'], 'proposed_core': d['core_ids'],
             'adopted_core': [ref for ref in d['core_ids'] if (d['day'], ref) in places]}
            for d in sketch['days']]


def additional_layouts(layouts, snapshot):
    """Try another nearby visit without displacing requested places or appointments."""
    originals = list(layouts['sketches'])
    region = layouts.get('regional_alternative')
    protected = {lock['id'] for lock in layouts.get('fixed_appointments', [])}
    protected.update(p['id'] for g in snapshot['place_groundings'] for p in g['records'])
    alternatives = []
    seen = set()
    for sketch in originals:
        occupied = {ref for day in sketch['days'] for ref in day['core_ids']}
        for index, day in enumerate(sketch['days']):
            movable = next((ref for ref in reversed(day['core_ids']) if ref not in protected), None)
            spare = next((ref for ref in day['alternative_ids'] if ref not in occupied), None)
            if not movable or not spare:
                continue
            days = deepcopy(sketch['days'])
            changed = days[index]
            changed['core_ids'][changed['core_ids'].index(movable)] = spare
            changed['alternative_ids'][changed['alternative_ids'].index(spare)] = movable
            identifier = _digest(days)
            if identifier not in seen:
                alternatives.append({**sketch, 'id':identifier, 'days':days, 'basis':'nearby_visit_substitution'})
                seen.add(identifier)
    return {**layouts, 'sketches': ([region] if region else []) + alternatives + originals}


def plan_from_sketches(spec, snapshot, layouts, env, facts, *, max_seconds=24, max_candidates=6, on_first=None,
                       finish_by=None, recovery_retry=False, regional_only=False, previous_entries=(),
                       targeted_trial=None, targeted_only=False):
    """Give each saved direction a slice of one search budget, then return checked candidates."""
    if not math.isfinite(max_seconds) or max_seconds <= 0 or type(max_candidates) is not int or max_candidates < 1:
        raise ValueError('试排时限和候选数量必须为正')
    check_snapshot_reference(spec, snapshot, facts)
    if (layouts['schema_version'] != 'trip_sketch_v1' or layouts['snapshot_id'] != snapshot['snapshot_id']
            or layouts['request_revision'] != snapshot['request_revision']
            or layouts['request_id'] != snapshot['request_id']):
        raise ValueError('构想必须引用该需求和快照')
    start = perf_counter()
    request, unsupported = constructor_input(spec, snapshot=snapshot)
    budgeted = 'budget' in request or 'scoped_budgets' in request
    frozen = frozen_city_outcome({'trip': {'origin_city': snapshot['origin'], 'destination_city': snapshot['destination']}})
    if frozen:
        return frozen
    if unsupported or layouts['state'] != 'ready_for_trial' or snapshot['origin'] == snapshot['destination']:
        issues = list(layouts.get('issues', []))
        if snapshot['origin'] == snapshot['destination']:
            issues.append({'code': 'same_city_trip_outside_trial_scope'})
        return {'state': 'needs_confirmation', 'message': '需确认当前试排器无法处理的要求后再试。',
                'issues': issues, 'evidence': {'unsupported_requirements': unsupported},
                'archive': [], 'model_calls': 0}
    names = {r['id']: r['name'] for rows in snapshot['candidates'].values() for r in rows}
    for grounding in snapshot['place_groundings']:
        names.update((r['id'], r['name']) for r in grounding['records'])
    for group in snapshot.get('experience_matches', {}).values():
        names.update((r['id'], r['name']) for r in group['records'])
    sketches = layouts['sketches']
    if request['profile']['pace'] == 'full':
        fuller = [expanded for sketch in sketches if (expanded := _full_pace_sketch(sketch))]
        # Give the original directions a turn even if the candidate budget is small.
        sketches = ([fuller[0], sketches[0], *fuller[1:], *sketches[1:]] if fuller else sketches)
    directions = [(sketch, _sketch_input(sketch, names)) for sketch in sketches]
    prepare_snapshot_routes(request, env, facts)
    search_start = perf_counter()
    deadline = search_start + max_seconds
    if finish_by is not None:
        deadline = min(deadline, finish_by)
    archive, diagnostics, checked = [], [], []
    route_cache = {}
    published = {_plan_signature(entry['itinerary']) for entry in previous_entries}
    first = None

    def try_direction(sketch, hints, seconds, styles):
        label = sketch['id'] if sketch else None
        inputs = {**request, **hints, 'search_styles': styles, 'explain_meal_failure': True}
        if wanted := request.get('preferred_attractions'):
            inputs['preferred_attractions'] = list(dict.fromkeys([*wanted, *hints.get('preferred_attractions', ())]))
        if sketch and sketch['source'] in LOCAL_LAYOUTS:
            inputs.update(profile={**request['profile'], 'restaurant_types': []},
                          search_hotel_names=hints['preferred_hotels'], try_preferred_meal_once=True,
                          prefer_activity_windows=True)
        elif sketch and sketch['source'] == 'nearby_regions':
            inputs.update(prefer_activity_windows=True, prefer_regional_activities=True)
        elif sketch and sketch['source'] == 'model' and request['profile']['pace'] == 'relaxed':
            inputs['prefer_activity_windows'] = True

        def consume(plan):
            nonlocal first
            signature = _plan_signature(plan)
            if signature in published or perf_counter() >= deadline:
                return
            began = perf_counter()
            validation, quality, places = audit(plan, request, env, facts)
            checked.append({'sketch_id': label, 'passed': validation['passed'], 'issues': validation['issues'],
                            'seconds': perf_counter() - began})
            if not validation['passed'] or len(archive) >= max_candidates:
                return
            published.add(signature)
            entry = {'id': _digest(plan), 'sketch_id': label, 'source': sketch['source'] if sketch else 'fallback',
                     'itinerary': plan, 'validation': validation, 'quality': quality, 'places': places,
                     'adoption': _adoption(sketch, {(p['day'], p['id']) for p in places}) if sketch else []}
            archive.append(entry)
            if first is None:
                first = perf_counter() - start
                if on_first:
                    on_first(entry)

        cap = 1 if sketch else min(2, max_candidates - len(archive))
        if previous_entries and not targeted_only and sketch and sketch.get('basis') != 'nearby_visit_substitution':
            # The first combination for an existing layout often already belongs to the archive.
            cap = min(3, max_candidates - len(archive))
        constructor = Constructor(inputs, env, seconds, cap, consume)
        constructor.deadline = min(constructor.deadline, deadline)
        constructor.routes = route_cache
        result = constructor.run()
        meal_failures = [a['meal_failure'] for a in result['diagnostics'] if a.get('meal_failure')]
        meal_rejections = Counter()
        for failure in meal_failures:
            meal_rejections.update(failure['restaurant_rejections'])
        diagnostics.append({'sketch_id': label, 'source': sketch['source'] if sketch else 'fallback',
                            'supplemented_for_pace': bool(sketch and sketch.get('supplemented_for_pace')),
                            'slice_seconds': round(seconds, 4), 'metrics': result['metrics'],
                            'reasons': dict(Counter(a['reason'] for a in result['diagnostics'] if a['reason'])),
                            'meal_rejections': dict(meal_rejections),
                            'first_meal_failure': meal_failures[0] if meal_failures else None})
        if anchor := inputs.get('arrival_access_anchor'):
            diagnostics[-1]['arrival_access_anchor'] = anchor
        if placements := inputs.get('earliest_required_visit_days'):
            diagnostics[-1]['earliest_required_visit_days'] = placements

    if recovery_retry and (remaining := deadline - perf_counter()) > 0:
        # Start from inexpensive, terminal-accessible combinations before trying layouts.
        try_direction(None, {'terminal_area_meals': True}, remaining, ('budget', 'short'))
    for index, (sketch, hints) in enumerate(directions if not (recovery_retry or targeted_only) else []):
        remaining = deadline - perf_counter()
        if remaining <= 0 or len(archive) >= max_candidates:
            break
        slice_seconds = min(6., remaining / (len(directions) - index + 1))
        styles = ('focus_budget', 'local', 'budget') if budgeted else ('local', 'cheap')
        if sketch['source'] in LOCAL_LAYOUTS | {'nearby_regions'}:
            styles = ('local', 'cheap')
        try_direction(sketch, hints, slice_seconds, styles)
    if not recovery_retry and not targeted_only and archive and not budgeted:
        # Try cost-focused alternatives once the initial layouts have had a turn.
        for sketch, hints in directions:
            remaining = deadline - perf_counter()
            if remaining <= 0 or len(archive) >= max_candidates:
                break
            try_direction(sketch, hints, min(3., remaining), ('focus_budget',))
    if (targeted_trial and not recovery_retry and (archive or previous_entries) and len(archive) < max_candidates
            and not layouts.get('fixed_appointments') and 2 <= request['days'] <= 3
            and any(item['source'].get('type') == 'map_selection' and item['source'].get('choice') == 'must'
                    for item in snapshot['requirements'])):
        remaining = deadline - perf_counter()
        anchors = request.get('must_attractions', ())
        if remaining > .5 and len(anchors) == 1 and directions:
            anchor = anchors[0]
            if targeted_trial == 'arrival_access':
                sketch, hints = directions[0]
                try_direction(sketch, {**hints, 'arrival_access_anchor': anchor}, min(3., remaining), ('local',))
            elif targeted_trial in ('visit_day', 'visit_day_full', 'visit_day_full_budget') and request['days'] == 3:
                for sketch, hints in directions:
                    if anchor in hints['day_preferences'][2]['attractions']:
                        if targeted_trial != 'visit_day' and (fuller := _full_pace_sketch(sketch)):
                            sketch, hints = fuller, _sketch_input(fuller, names)
                        style = 'focus_budget' if budgeted and targeted_trial == 'visit_day_full_budget' else 'local'
                        try_direction(sketch, {**hints, 'earliest_required_visit_days': {anchor: 2}},
                                      min(3., remaining), (style,))
                        break
    remaining = deadline - perf_counter()
    if not (regional_only or recovery_retry or targeted_only) and not archive and remaining > 0:
        try_direction(None, {}, remaining, ('cheap', 'budget', 'local') if budgeted else ('local', 'cheap', 'short'))
    remaining = deadline - perf_counter()
    if not (regional_only or recovery_retry or targeted_only) and not archive and remaining > 0 and any(
            d['meal_rejections'].get('meal_to_next_required_stop') for d in diagnostics):
        try_direction(None, {'terminal_area_meals': True}, remaining, ('local', 'cheap'))
    elapsed = perf_counter() - start
    stop = ('candidate_limit' if len(archive) >= max_candidates else 'deadline'
            if perf_counter() >= deadline else 'trial_completed')
    exhausted_slice = any(d['metrics']['budget_exhausted'] for d in diagnostics)
    state = 'complete' if archive else 'search_timeout' if stop == 'deadline' or exhausted_slice else 'no_verified_candidate'
    return {'schema_version': 'sketch_trial_v1', 'state': state, 'snapshot_id': snapshot['snapshot_id'],
            'message': '已产生快照下通过核验的候选。' if archive else '本轮试排没有得到通过核验的候选。',
            'stop_reason': stop, 'archive': archive, 'checked': checked, 'diagnostics': diagnostics,
            'first_verified_seconds': first, 'planning_seconds': elapsed,
            'preparation_seconds': search_start - start, 'search_seconds': elapsed - (search_start - start),
            'verification_seconds': sum(c['seconds'] for c in checked),
            'route_calls': sum(d['metrics']['route_calls'] for d in diagnostics), 'model_calls': 0}


def _rebuild_day(traveler, original, order, previous_hotel, locked, facts, prefer_cheaper):
    day, activities = original['day'], original['activities']
    start = activities[0]
    leading_ticket = day == 1 and start['type'] in INTERCITY
    if leading_ticket:
        now, position, new = minutes(start['end_time']), start['end'], [start]
    else:
        first_leg = start['transports'][0]['start_time'] if start['transports'] else start['start_time']
        now, position, new = min(510, minutes(first_leg)), previous_hotel, []
    for index in order[int(leading_ticket):]:
        activity = activities[index]
        kind = activity['type']
        begin, end = minutes(activity['start_time']), minutes(activity['end_time'])
        duration = end - begin
        target = activity['start'] if kind in INTERCITY else activity['position']
        free_visit = kind == 'attraction' and (target, activity['start_time']) not in locked
        if free_visit:
            item = facts.place(traveler.city, target, 'attraction')
            if item['status'] != 'found':
                return None
            windows = item['records'][0]['opening']['windows_minutes']
            suggested = traveler.request.get('experience_earliest', {}).get(target)
            if suggested:
                windows = [(max(lo, minutes(suggested)), hi) for lo, hi in windows]
            latest = max((hi - duration for _, hi in windows), default=-1)
        elif kind == 'accommodation':
            latest = 1320
        elif kind in INTERCITY:
            latest = begin - (60 if kind == 'train' else 120)
        else:
            latest = begin
        options = traveler.routes_to(position, target, now, latest, 'local')
        if not options:
            return None
        def rank(option):
            cost = sum(leg['cost'] for leg in option[2])
            return (cost, option[1]) if prefer_cheaper else (option[1], cost)

        _, reached, legs = min(options, key=rank)
        if free_visit:
            begins = [max(reached, lo) for lo, hi in windows if max(reached, lo) + duration <= min(hi, 1440)]
            if not begins:
                return None
            begin = min(begins)
            end = begin + duration
        elif kind == 'accommodation':
            begin = reached
        new.append({**activity, 'start_time': clock(begin), 'end_time': clock(end), 'transports': legs})
        now, position = end, target
    return {'day': day, 'activities': new}


def _day_points(activities, previous_hotel, order):
    points = [previous_hotel if previous_hotel is not None else activities[0]['end']]
    for index in order[int(previous_hotel is None):]:
        activity = activities[index]
        points.append(activity['start'] if activity['type'] in INTERCITY else activity['position'])
    return points


def _nearby_orders(original, movable):
    seen = {original}
    for first, last in combinations(range(len(movable)), 2):
        for reverse in (False, True):
            visits = list(movable)
            if reverse:
                visits[first:last + 1] = reversed(visits[first:last + 1])
            else:
                visits[first], visits[last] = visits[last], visits[first]
            order = list(original)
            for slot, index in zip(movable, visits):
                order[slot] = index
            order = tuple(order)
            if order not in seen:
                seen.add(order)
                yield order


def optimize_day(spec, snapshot, plan, day, env, facts, *, max_seconds=3, view_only=False, prefer_faster=False):
    """Compare straight-line distance and independently verify any new order."""
    check_snapshot_reference(spec, snapshot, facts)
    if type(day) is not int or not 1 <= day <= snapshot['days'] or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError('顺路优化需要行程范围内的日序和正的计算时限')
    request, unsupported = constructor_input(spec, snapshot=snapshot)
    if unsupported:
        raise ValueError('当前顺路优化无法核验这份需求')
    prepare_snapshot_routes(request, env, facts)
    validation, before, _ = audit(plan, request, env, facts)
    if not validation['passed']:
        return {'state': 'input_not_verified', 'issues': validation['issues'], 'model_calls': 0}
    original = plan['itinerary'][day - 1]
    previous = plan['itinerary'][day - 2]['activities'][-1]['position'] if day > 1 else None
    locked = {(a['place'], a['start_time']) for a in request['appointments'] if a['day'] == day}
    movable = [i for i, a in enumerate(original['activities'])
               if a['type'] == 'attraction' and (a['position'], a['start_time']) not in locked]
    traveler = Constructor(request, env, max_seconds, 1)
    best, best_report, best_quality = plan, validation, before
    activities = original['activities']
    order = tuple(range(len(activities)))
    points = _day_points(activities, previous, order)
    missing = sorted(set(points) - traveler.points.keys())
    distances = {}

    def distance(indices):
        locations = _day_points(activities, previous, indices)
        total = 0.
        for start, end in zip(locations, locations[1:]):
            key = tuple(sorted((start, end)))
            if key not in distances:
                distances[key] = traveler.distance(start, end)
            total += distances[key]
        return total

    baseline_km = best_km = distance(order) if not missing else None
    trials = 0
    stopped = False
    rejected = Counter()
    if not view_only and not missing:
        try:
            layouts = []
            for indices in _nearby_orders(order, movable):
                traveler.check()
                km = distance(indices)
                if km < baseline_km - 1e-8:
                    layouts.append((km, indices))
            for km, indices in sorted(layouts):
                traveler.check()
                for prefer_cheaper in (False, True):
                    rebuilt = _rebuild_day(traveler, original, indices, previous, locked, facts, prefer_cheaper)
                    trials += 1
                    if rebuilt is None:
                        rejected['no_route_within_time_window'] += 1
                        continue
                    trial = deepcopy(plan)
                    trial['itinerary'][day - 1] = rebuilt
                    report, quality, _ = audit(trial, request, env, facts)
                    if report['passed']:
                        if not prefer_faster or quality['city_transit_minutes'] < before['city_transit_minutes']:
                            best, best_report, best_quality, best_km = trial, report, quality, km
                            break
                        continue
                    rejected.update(issue['code'] for issue in report['issues'])
                if best is not plan:
                    break
        except SearchBudgetExpired:
            stopped = True
    state = ('viewed' if view_only else 'missing_coordinates' if missing else
             'improved' if best is not plan else 'kept_verified_order')
    return {'state': state, 'day': day,
            'before': before, 'after': best_quality, 'itinerary': best, 'validation': best_report,
            'distance': {'method': 'straight_line', 'before_km': baseline_km, 'after_km': best_km,
                         'missing_places': missing},
            'stop_reason': 'deadline' if stopped else 'trial_completed', 'trials': trials,
            'rejections': dict(rejected), 'route_calls': traveler.route_calls, 'model_calls': 0}


def day_transport_options(spec, plan, day, env, *, max_seconds=4):
    """Estimate the three supported option categories between adjacent stops."""
    request, _ = constructor_input(spec)
    traveler = Constructor(request, env, max_seconds, 1)
    allowed = request.get('local_modes', ('metro', 'taxi', 'walk'))
    days = plan['itinerary']
    activities = days[day - 1]['activities']
    previous = days[day - 2]['activities'][-1]['position'] if day > 1 else None
    points = _day_points(activities, previous, tuple(range(len(activities))))
    segments = []
    for index, (start, end) in enumerate(zip(points, points[1:]), start=int(previous is None)):
        if start == end:
            continue
        activity = activities[index]
        actual = activity['transports']
        departure = minutes(actual[0]['start_time'] if actual else activity['start_time'])
        options = []
        for mode in ('metro', 'taxi', 'walk'):
            option = {'mode': mode}
            if mode not in allowed:
                option['status'] = 'restricted'
            else:
                try:
                    legs = traveler.route(start, end, departure, mode)
                except SearchBudgetExpired:
                    option['status'] = 'deadline'
                else:
                    if (not legs or (mode == 'metro' and not any(t['mode'] == 'metro' for t in legs))
                            or any(t['mode'] not in allowed for t in legs)):
                        option['status'] = 'unavailable'
                    else:
                        option.update(status='estimated', duration_minutes=minutes(legs[-1]['end_time']) - departure,
                                      source='snapshot_taxi' if mode == 'taxi' else 'snapshot',
                                      party_cost_cny=round(sum(t['cost'] for t in legs), 2),
                                      legs=[{'from': t['start'], 'to': t['end'], 'mode': t['mode']} for t in legs])
            options.append(option)
        km = traveler.distance(start, end)
        segments.append({'activity_index': index, 'from': start, 'to': end,
                         'straight_line_km': km if math.isfinite(km) else None, 'options': options})
    return segments, traveler.route_calls
