"""Independently verify an archive and compare eligible plans on observable dimensions."""

from collections import defaultdict
from time import perf_counter

from .planning_compat import (ARRIVAL_PERIODS, ARRIVAL_STATION_ALIASES, BUDGET_EXCLUSIONS, audit, check_snapshot_reference,
                              constructor_input, local_mode_options, prepare_snapshot_routes)
from .trip_world import (exemplar_evidence, requested_experience_match, hotel_feature_options, matches_tags,
                         preferred_tag_terms, theme_name_matches)
from .tripspec import BUDGET_SCOPE_LABELS, relative_start
from .world_facts import TICKET_OPTIONS, clock_minutes, ticket_option
from chinatravel.planning.engine import total_cost


INTERCITY = {'train', 'airplane'}
MEALS = {'breakfast', 'lunch', 'dinner'}
NAMED = {'attraction': 'attraction', 'restaurant': 'restaurant', 'hotel': 'accommodation'}
TAGGED = {'experience_theme': 'attraction', 'cuisine': 'restaurant', 'hotel_feature': 'accommodation'}
LAYOUT_SCOPES = {'total_trip', 'local_experience', 'food', 'lodging'}
MEASURED = {'transit_time': 'city_transit_minutes', 'walking_experience': 'access_walk_minutes',
            'lodging_cost': 'lodging_cost_cny', 'total_cost': 'total_cost_cny',
            'restaurant_access_time': 'meal_access_minutes', 'attraction_count': 'distinct_attractions',
            'food_cost': 'meal_cost_cny', 'food_spend_share': 'meal_share'}
COMPARISONS = {'total_cost_cny': 100, 'city_transit_minutes': 15, 'access_walk_minutes': 15,
               'visit_minutes': 30}
DEFAULT_TRANSFER_TOLERANCE_MINUTES = 15
MESSAGE = {'budget': '整团参考总费用', 'intercity_mode': '往返班次类型', 'room_count': '每晚房间数',
           'nights': '住宿晚数', 'hotel_feature': '酒店设施源记录',
           'hotel': '指定酒店实体', 'lodging_in_plan': '各晚已安排的住宿', 'lodging_budget': '每晚住宿参考费用',
           'meal_budget': '每餐人均参考价', 'appointment': '固定地点、日期和时段',
           'local_mode': '市内交通方式'}


def _duration(activity):
    return clock_minutes(activity['end_time']) - clock_minutes(activity['start_time'])


def _values(requirement):
    value = requirement['value']
    return value if isinstance(value, list) else [value]


def _hard_checks(requirements, unsupported, plan, report, people, hotel_pins=None, visit_pins=None, excluded=None):
    items = [r for r in requirements if (r['strength']=='hard' or r['field']=='attraction' and r['strength']=='fact')
             and r['field'] != 'room_type']
    observations = report.get('snapshot_evidence', {})
    stays = observations.get('overnight_stays', [])
    trips = observations.get('tickets', [])
    entries = [(d['day'], a) for d in plan['itinerary'] for a in d['activities']] if observations else []
    issues = {i['code'] for i in report['issues']}
    nights = list(range(1, len(plan['itinerary'])))
    all_nights = bool(nights) and sorted(s['day'] for s in stays) == nights
    result = []
    for req in items:
        field, value, scope = req['field'], req['value'], req['scope']
        actual, passed = None, None
        is_supported = not any(all(req[k] == other[k] for k in ('field', 'value', 'scope', 'operator'))
                               for other in unsupported)
        if not is_supported:
            reason = 'unsupported_requirement'
        else:
            reason = 'evidence_unavailable'
            if field == 'budget' and report.get('total_cost') is not None:
                limit = value * people if req['unit'] == 'CNY/person' else value
                amount = report['total_cost']
                if scope in BUDGET_EXCLUSIONS:
                    amount = round(total_cost((activity for d in plan['itinerary'] for activity in d['activities']),
                                              exclude=BUDGET_EXCLUSIONS[scope]), 2)
                actual = {'cny_per_group': amount, 'limit': limit}
                passed = amount <= limit + .01
                if issues.intersection({'price_evidence', 'activity_cost', 'hotel_cost', 'meal_cost', 'transport_cost',
                                        'ticket_price_evidence', 'poi_evidence', 'ticket_evidence', 'cost'}):
                    passed = None
            elif field == 'intercity_mode':
                directions = (scope,) if scope in ('outbound', 'return') else ('outbound', 'return')
                actual = {d: [r for group in trips if group['direction'] == d for r in group['records']]
                          for d in directions}
                if all(actual.values()):
                    passed = all(any(ticket_option(opt, r['mode'], {'TrainType': r['train_type'], 'TrainID': r['service']})
                                     for r in records for opt in _values(req) if opt in TICKET_OPTIONS)
                                 for records in actual.values())
            elif field in ('arrival_station', 'arrival_time'):
                actual = [r for group in trips if group['direction'] == 'outbound' for r in group['records']]
                if actual and field == 'arrival_station':
                    passed = all(r['to'] == ARRIVAL_STATION_ALIASES.get(value, value) for r in actual)
                elif actual:
                    begin, end = ARRIVAL_PERIODS[value]
                    passed = all(begin <= r['arrival'] < end for r in actual)
            elif field == 'local_mode' and observations:
                allowed = local_mode_options(req)
                legs = [{'day': day, 'from': leg['start'], 'to': leg['end'], 'mode': leg['mode']}
                        for day, activity in entries for leg in activity['transports']]
                violations = [leg for leg in legs if leg['mode'] not in allowed]
                actual = {'allowed': sorted(allowed), 'used': sorted({leg['mode'] for leg in legs}),
                          'checked_legs': len(legs), 'violations': violations}
                passed = not violations
            elif field == 'appointment' and isinstance(value, dict) and observations:
                day, begin = value['day'], clock_minutes(value['start_time'])
                actual = [{'day': d, 'place': a.get('position'), 'start_time': a['start_time'],
                           'end_time': a['end_time']} for d, a in entries
                          if a['type'] == 'attraction' and a.get('position') == value['name']]
                passed = any(a['day'] == day and clock_minutes(a['start_time']) == begin
                             and clock_minutes(a['end_time']) == begin + value['duration_minutes'] for a in actual)
            elif field == 'room_count' and observations:
                actual = [{'day': d, 'value': a['rooms']} for d, a in entries if a['type'] == 'accommodation']
                passed = all_nights and all(a['value'] == value for a in actual)
            elif field == 'nights' and observations:
                actual = sorted(s['day'] for s in stays)
                passed = actual == nights and len(actual) == value
            elif field == 'lodging_in_plan' and observations:
                actual = {'expected_nights': nights, 'verified_nights': [s['day'] for s in stays]}
                passed = all_nights
            elif field == 'hotel':
                pin = (hotel_pins or {}).get(req['id'])
                actual = {'requested_place': pin, 'overnight_places': [
                    {'day': s['day'], 'id': s['id'], 'name': s['name']} for s in stays]}
                if pin and observations:
                    passed = all_nights and all(s['id'] == pin['id'] for s in stays)
            elif field == 'attraction':
                pin=(visit_pins or {}).get(req['id'])
                actual=observations.get('attraction_ids',[])
                if req['operator'] == 'exclude':
                    excluded_id = (excluded or {}).get(req['id'])
                    if excluded_id and observations:
                        passed = excluded_id not in actual
                elif pin:
                    passed=pin['id'] in actual
            elif field == 'lodging_budget':
                divisor = people if req['unit'] == 'CNY/person/night' else 1
                actual = [{'day': d, 'value': a['price'] if req['unit'] == 'CNY/room/night'
                           else a['price'] * a['rooms'] / divisor} for d, a in entries
                          if a['type'] == 'accommodation']
                if observations and not issues.intersection({'price_evidence', 'hotel_cost', 'poi_evidence'}):
                    passed = all_nights and len(actual) == len(nights) and all(x['value'] <= value + 1e-6 for x in actual)
            elif field == 'hotel_feature':
                actual = [{'day': s['day'], 'hotel': s['id'], 'values': s['features']['values']} for s in stays]
                if actual and all(a['values'] for a in actual):
                    passed = all_nights and all(set(hotel_feature_options(value)).intersection(a['values']) for a in actual)
            elif field == 'meal_budget':
                actual = [{'day': d, 'meal': a['type'], 'cny_per_person': a['price']} for d, a in entries
                          if a['type'] in MEALS]
                if observations and not issues.intersection({'poi_evidence', 'price_evidence'}):
                    passed = 'missing_meal' not in issues and all(a['cny_per_person'] <= value for a in actual)
            if passed is not None:
                reason = None if passed else 'requirement_violated'
        status = 'passed' if passed is True else 'violated' if passed is False else 'insufficient_evidence'
        label = BUDGET_SCOPE_LABELS.get(scope, MESSAGE.get(field, field)) if field == 'budget' else MESSAGE.get(field, field)
        result.append({'requirement_id': req['id'], 'field': field, 'label': label,
                       'status': status, 'reason': reason, 'expected': value, 'observed': actual,
                       'unit': req['unit'], 'scope': scope, 'source': req['source']})
    return result


def _metrics(plan, baseline):
    costs = dict.fromkeys(('intercity', 'admission', 'meals', 'lodging', 'city_transit'), 0.)
    meals, days = [], []
    for day in plan['itinerary']:
        visits = transfers = walking = count = longest = terminal_access = 0
        activities = day['activities']
        for index, activity in enumerate(activities):
            kind = activity['type']
            key = 'intercity' if kind in INTERCITY else 'meals' if kind in MEALS else (
                'lodging' if kind == 'accommodation' else 'admission')
            costs[key] += activity['cost']
            legs = activity['transports']
            approach = sum(_duration(leg) for leg in legs)
            costs['city_transit'] += sum(leg['cost'] for leg in legs)
            transfers += approach
            if kind in INTERCITY or (index and activities[index - 1]['type'] in INTERCITY):
                terminal_access += approach
            longest = max(longest, approach)
            walking += sum(_duration(leg) for leg in legs if leg['mode'] == 'walk')
            if kind == 'attraction':
                visits += _duration(activity)
                count += 1
            if kind in MEALS:
                meals.append({'day': day['day'], 'name': activity['position'], 'cost_cny': activity['cost'],
                              'pax_cny': activity['price'], 'approach_minutes': approach})
        days.append({'day': day['day'], 'visit_minutes': visits, 'city_transit_minutes': transfers,
                     'access_walk_minutes': walking, 'attractions': count, 'longest_transfer_minutes': longest,
                     'terminal_access_minutes': terminal_access, 'other_city_transit_minutes': transfers - terminal_access})
    return {**baseline, 'cost_breakdown_cny': {key: round(value, 2) for key, value in costs.items()},
            'meal_cost_cny': round(costs['meals'], 2), 'lodging_cost_cny': round(costs['lodging'], 2),
            'meal_share': round(costs['meals'] / baseline['total_cost_cny'], 4) if baseline['total_cost_cny'] else None,
            'meal_access_minutes': round(sum(m['approach_minutes'] for m in meals) / len(meals), 1) if meals else None,
            'highest_cost_meal': max(meals, key=lambda m: m['cost_cny'], default=None),
            'peak_daily_city_transit_minutes': max(d['city_transit_minutes'] for d in days),
            'terminal_access_minutes': sum(d['terminal_access_minutes'] for d in days),
            'other_city_transit_minutes': sum(d['other_city_transit_minutes'] for d in days),
            'longest_transfer_minutes': max(d['longest_transfer_minutes'] for d in days),
            'full_visit_days': sum(d['attractions'] >= 2 or d['visit_minutes'] >= 180 for d in days),
            'days': days}


def _goal_results(snapshot, places, vector):
    by_goal = defaultdict(list)
    for row in snapshot['place_groundings']:
        by_goal[row['requirement_id']].append(row)
    results = []
    for req in snapshot['requirements']:
        example = req['field'] == 'experience_example' and req['operator'] == 'example_of'
        if (req['strength'] != 'soft' and not (example and req['strength'] == 'fact')) or req['field'] == 'room_type':
            continue
        field, op = req['field'], req['operator']
        status, reason, hits = 'insufficient_evidence', 'goal_not_measurable_from_snapshot', []
        metric, direction = None, None
        evidence_tags = {}
        observed = None
        if op in ('no_preference', 'unspecified'):
            status, reason = 'not_applicable', None
        elif req.get('open_experience'):
            hits = [p for p in places if p['id'].startswith('attraction:') and
                    requested_experience_match(req, p['name'], p['tags'], snapshot['destination'])]
            if req.get('preferred_start'):
                hits = [p for p in hits if p.get('start_time', '') >= req['preferred_start']]
                evidence_tags['preferred_start'] = req['preferred_start']
            observed = len({p['id'] for p in hits})
            known = snapshot.get('experience_matches', {}).get(req['id'], {}).get('matched_count', 0)
            status, reason = ('matched', None) if observed else (
                ('unmet', 'matching_places_not_scheduled') if known else
                ('insufficient_evidence', 'no_schedulable_catalog_match'))
        elif example and req['scope'] == 'total_trip':
            ids, tags = exemplar_evidence(by_goal[req['id']])
            stops = [p for p in places if p['id'].startswith('attraction:')]
            hits = [p for p in stops if p['id'] in ids or matches_tags(p['tags'], tags, ())]
            evidence_tags = {'exemplar_ids': sorted(ids), 'resolved_tags': sorted(tags)}
            if hits or (tags and stops and all(p['tags'] for p in stops)):
                status, reason = ('matched' if hits else 'unmet'), None
        elif field in NAMED and req['scope'] in LAYOUT_SCOPES:
            matches = by_goal[req['id']]
            exact = {p['id'] for g in matches if g['status'] == 'found' for p in g['records']}
            related = {p['id'] for g in matches if g['status'] in ('ambiguous', 'unconfirmed_match')
                       for p in g['records']}
            hits = [p for p in places if p['id'] in exact or p['id'] in related]
            exact_hit = any(p['id'] in exact for p in hits)
            if exact_hit and op not in ('avoid', 'exclude'):
                status, reason = 'matched', None
            elif related:
                reason = 'ambiguous_reference'
            elif exact:
                status, reason = ('unmet' if exact_hit == (op in ('avoid', 'exclude')) else 'matched'), None
            else:
                reason = 'missing_entity_evidence'
        elif field in TAGGED and req['scope'] in LAYOUT_SCOPES:
            rows = [p for p in places if p['id'].startswith(TAGGED[field] + ':')]
            values = [v.strip() for v in _values(req) if isinstance(v, str) and v.strip()]
            exact_tags, literal_terms = preferred_tag_terms(field, values, snapshot.get('destination'))
            hits = [p for p in rows if matches_tags(p['tags'], exact_tags, literal_terms) or
                    (field=='experience_theme' and theme_name_matches(p['name'],values))]
            evidence_tags = {'resolved_tags': sorted(exact_tags), 'literal_terms': sorted(literal_terms)}
            if (exact_tags or literal_terms) and (hits or (rows and all(row['tags'] for row in rows))):
                status, reason = ('matched' if bool(hits) != (op in ('avoid', 'exclude')) else 'unmet'), None
        else:
            if field == 'pace' and req['value'] == '舒缓' and op in ('prefer', 'prioritize', 'eq'):
                metric, direction = 'peak_daily_city_transit_minutes', 'minimize'
            elif field == 'pace' and req['value'] == '充实' and op in ('prefer', 'prioritize', 'eq'):
                metric, direction = 'full_visit_days', 'maximize'
            elif field in MEASURED and op in ('minimize', 'maximize'):
                metric, direction = MEASURED[field], op
            if metric and vector.get(metric) is not None:
                status, reason = 'measured', None
                observed = vector[metric]
        results.append({'requirement_id': req['id'], 'field': field, 'operator': op, 'value': req['value'],
                        'source': req['source'], 'status': status, 'reason': reason,
                        'matched_places': hits, 'metric': metric, 'direction': direction,
                        'observed': observed, 'primary': req.get('primary', False),
                        'maximize_matches': req.get('maximize_matches', False), **evidence_tags})
    return results


def _named_matches(candidate):
    return sum(g['field'] in NAMED and g['status'] == 'matched' for g in candidate['goals'])


def _objectives(candidate, common_measures):
    q = candidate['quality_vector']
    goals = candidate['goals']
    priorities = sum(g['operator'] == 'prioritize' and g['status'] == 'matched' for g in goals)
    measures = tuple(g['observed'] * (-1 if g['direction'] == 'maximize' else 1) for g in goals
                     if g['requirement_id'] in common_measures)
    counts = tuple(-g['observed'] for g in goals if g.get('maximize_matches') and g['observed'] is not None)
    return (-priorities, -_named_matches(candidate), -sum(g['status'] == 'matched' for g in goals), *measures,
            *counts, q['total_cost_cny'], q['city_transit_minutes'], q['access_walk_minutes'], -q['visit_minutes'])


def _dominates(left, right, common_measures):
    pairs = list(zip(_objectives(left, common_measures), _objectives(right, common_measures)))
    return all(a <= b for a, b in pairs) and any(a < b for a, b in pairs)


def select_candidates(eligible, *, initial_recommended_id=None, keep_recommended_id=None):
    if not eligible:
        return {'recommended_id': None, 'alternatives': [], 'frontier_ids': [], 'reason': 'no_verified_candidates'}
    common_measures = {g['requirement_id'] for g in eligible[0]['goals'] if g['status'] == 'measured'}
    for candidate in eligible[1:]:
        common_measures.intersection_update(g['requirement_id'] for g in candidate['goals'] if g['status'] == 'measured')
    frontier = [c for c in eligible if not any(
        _dominates(other, c, common_measures) for other in eligible if other is not c
    )]
    pace_goals = {g['requirement_id'] for g in eligible[0]['goals']
                  if g.get('metric') == 'full_visit_days'} & common_measures
    large_detours = set()
    if pace_goals:
        comparable = common_measures - pace_goals
        for candidate in eligible:
            current = candidate['quality_vector']
            current_goals = _objectives(candidate, comparable)[:-4]
            for other in frontier:
                leaner = other['quality_vector']
                if (not 0 < leaner['full_visit_days'] < current['full_visit_days']
                        or _objectives(other, comparable)[:-4] != current_goals):
                    continue
                extra_travel = (current.get('other_city_transit_minutes', current['city_transit_minutes'])
                                - leaner.get('other_city_transit_minutes', leaner['city_transit_minutes']))
                extra_visit = max(0, current['visit_minutes'] - leaner['visit_minutes'])
                if extra_travel >= max(60, extra_visit + 30):
                    large_detours.add(candidate['id'])
                    break
    minimum_by_goals = {}
    for candidate in eligible:
        *goals, _, travel, _, _ = _objectives(candidate, common_measures)
        key = tuple(goals)
        minimum_by_goals[key] = min(travel, minimum_by_goals.get(key, travel))

    def order(candidate):
        *goals, cost, travel, walk, visit = _objectives(candidate, common_measures)
        preference = ((*_objectives(candidate, common_measures-pace_goals)[:-4],
                       candidate['id'] in large_detours, *goals) if large_detours else tuple(goals))
        gap = travel - minimum_by_goals[tuple(goals)]
        if gap <= DEFAULT_TRANSFER_TOLERANCE_MINUTES:
            return (*preference, 0, cost, travel, walk, visit, candidate['id'])
        return (*preference, 1, travel, cost, walk, visit, candidate['id'])

    ranked = sorted(eligible, key=order)
    pick = next(c for c in ranked if c in frontier) if large_detours else ranked[0]
    if keep_recommended_id is not None:
        pick = next((candidate for candidate in eligible if candidate['id'] == keep_recommended_id), pick)
    # A fuller trip can trade a small part of the price for a substantial nearby visit.
    # First preserve each explicitly measured preference, including cheap or low-walking trips.
    original_pick = pick
    for candidate in frontier if keep_recommended_id is None else ():
        if candidate.get('visit_refined_from') != original_pick['id']:
            continue
        if candidate['id'] in large_detours:
            continue
        before = original_pick['quality_vector']
        after = candidate['quality_vector']
        preserved = all(a <= b for a, b in zip(_objectives(candidate, common_measures)[:-4],
                                                _objectives(original_pick, common_measures)[:-4]))
        if (preserved and after['visit_minutes'] >= before['visit_minutes'] + 60
                and after['city_transit_minutes'] <= before['city_transit_minutes'] + 15
                and after['access_walk_minutes'] <= before['access_walk_minutes'] + 12
                and after['total_cost_cny'] <= before['total_cost_cny'] + min(120, before['total_cost_cny'] * .025)):
            pick = candidate
            break
    options = [candidate for candidate in ranked if candidate is not pick and candidate in frontier]
    if large_detours:
        fuller = next((c for c in options if c['quality_vector']['full_visit_days'] >
                       pick['quality_vector']['full_visit_days']), None)
        if fuller:
            options.remove(fuller)
            options.insert(0, fuller)
    # When extra search changes the recommendation, keep a competitive first-round option
    # available for comparison. The normal material-difference test below still applies.
    initial = next((c for c in options if c['id'] == initial_recommended_id), None)
    if initial:
        options.remove(initial)
        options.insert(0, initial)
    alternatives = []
    retained = []
    pick_matched = {goal['requirement_id'] for goal in pick['goals'] if goal['status'] == 'matched'}
    for option in options:
        delta = {key: round(option['quality_vector'][key] - pick['quality_vector'][key], 2)
                 for key in COMPARISONS}
        gains = [key for key, gap in delta.items() if
                 (gap if key == 'visit_minutes' else -gap) >= COMPARISONS[key]]
        if pace_goals:
            day_change = option['quality_vector']['full_visit_days'] - pick['quality_vector']['full_visit_days']
            delta['full_visit_days'] = day_change
            if day_change > 0:
                gains.append('full_visit_days')
        matched = {goal['requirement_id'] for goal in option['goals'] if goal['status'] == 'matched'}
        different_preference = bool(matched - pick_matched)
        too_similar = any(
            matched == previous_goals
            and all(abs(option['quality_vector'][key] - previous['quality_vector'][key]) < threshold
                    for key, threshold in COMPARISONS.items())
            and (not pace_goals or option['quality_vector']['full_visit_days'] == previous['quality_vector']['full_visit_days'])
            for previous, previous_goals in retained)
        if not too_similar and (gains or different_preference or (pick is not original_pick and option is original_pick)):
            alternatives.append({'id': option['id'], 'delta_from_recommended': delta, 'improvements': gains,
                                 'matched_named_goal_delta': _named_matches(option) - _named_matches(pick)})
            retained.append((option, matched))
        if len(alternatives) == 2:
            break
    return {'recommended_id': pick['id'], 'frontier_ids': [e['id'] for e in frontier], 'alternatives': alternatives,
            'reason': 'only_verified_candidate' if len(eligible) == 1 else 'preference_then_travel_and_cost',
            'default_transfer_tolerance_minutes': DEFAULT_TRANSFER_TOLERANCE_MINUTES,
            'reasons': {'matched_goal_ids': [g['requirement_id'] for g in pick['goals'] if g['status'] == 'matched'],
                        'prioritized_measures': [g for g in pick['goals'] if g['requirement_id'] in common_measures],
                        'observations': {key: pick['quality_vector'][key] for key in COMPARISONS}}}


def evaluate_archive(spec, snapshot, archive, env, facts):
    start = perf_counter()
    check_snapshot_reference(spec, snapshot, facts)
    if archive['schema_version'] != 'sketch_trial_v1' or archive['snapshot_id'] != snapshot['snapshot_id']:
        raise ValueError('候选档案必须引用同一个 WorldSnapshot')
    ids = [item['id'] for item in archive['archive']]
    if len(ids) != len(set(ids)):
        raise ValueError('候选档案包含重复编号')
    request, unsupported = constructor_input(spec, snapshot=snapshot)
    if archive['archive']:
        prepare_snapshot_routes(request, env, facts)
    ready = perf_counter()
    candidates = []
    for item in archive['archive']:
        plan = item['itinerary']
        report, baseline, places = audit(plan, request, env, facts)
        hard = _hard_checks(snapshot['requirements'], unsupported, plan, report, snapshot['people'],
                            request.get('required_hotel_ids'), request.get('required_visit_ids'),
                            request.get('excluded_visit_ids'))
        if any(r['status'] != 'passed' for r in hard):
            report['issues'].append({'code': 'requirement_evidence', 'requirement_ids':
                                     [r['requirement_id'] for r in hard if r['status'] != 'passed']})
        report['passed'] = report['passed'] and not report['issues']
        report['hard_requirements'] = hard
        candidate = {'id': item['id'], 'source': item.get('source'), 'validation_report': report}
        if item.get('visit_refined_from'):
            candidate['visit_refined_from'] = item['visit_refined_from']
        if report['passed']:
            vector = _metrics(plan, baseline)
            candidate.update(quality_vector=vector, goals=_goal_results(snapshot, places, vector), places=places,
                             pending_verifications=['current_ticket_and_room_availability', 'date_specific_prices_and_opening',
                                                    'inside_attraction_walking'])
            if report['snapshot_evidence']['overnight_stays']:
                candidate['pending_verifications'].append('actual_room_type_and_occupancy')
            if any(r['field'] == 'start_date' and relative_start(r['value']) for r in snapshot['requirements']):
                candidate['pending_verifications'].append('calendar_start_date')
            if not snapshot.get('requested_start_date') and any(
                    r['field'] == 'date_scope' and r['value'] == '周末' for r in snapshot['requirements']):
                candidate['pending_verifications'].append('weekend_date')
            if any(p['id'].startswith('attraction:') and p['name'] in request.get('experience_earliest', {})
                   for p in places):
                candidate['pending_verifications'].append('specific_experience_departure')
        candidates.append(candidate)
    eligible = [c for c in candidates if c['validation_report']['passed']]
    return {'schema_version': 'plan_assessment_v1', 'snapshot_id': snapshot['snapshot_id'],
            'state': 'complete' if eligible else 'no_verified_candidate', 'candidate_count': len(candidates),
            'verified_count': len(eligible), 'candidates': candidates,
            'selection': select_candidates(eligible, initial_recommended_id=archive.get('initial_recommended_id')),
            'preparation_seconds': ready - start, 'assessment_seconds': perf_counter() - ready, 'model_calls': 0}
