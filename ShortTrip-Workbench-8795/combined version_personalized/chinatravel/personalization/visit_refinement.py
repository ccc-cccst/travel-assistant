"""Try nearby visits in genuine gaps of a verified itinerary."""

from copy import deepcopy
from time import perf_counter

from chinatravel.planning.engine import Constructor, SearchBudgetExpired, clock, minutes, total_cost
from .meal_refinement import _route_minutes
from .plan_assessment import evaluate_archive, select_candidates
from .planning_compat import constructor_input
from .sketch_planning import _digest


def refine_visits(spec, snapshot, archive, assessment, env, facts, seconds):
    start = perf_counter()
    parent_id = assessment['selection']['recommended_id']
    log = {'parent': parent_id, 'route_calls': 0, 'changes': [], 'model_calls': 0}
    def finish(reason):
        return archive, assessment, {**log, 'state': reason, 'seconds': perf_counter() - start}

    if not parent_id or seconds < 1:
        return finish('no_time_or_parent')
    request, _ = constructor_input(spec, snapshot=snapshot)
    pace = request['profile']['pace']
    if pace == 'relaxed':
        return finish('relaxed_pace')
    parent = next(c for c in archive['archive'] if c['id'] == parent_id)
    old = next(c for c in assessment['candidates'] if c['id'] == parent_id)
    plan = deepcopy(parent['itinerary'])
    traveler = Constructor(request, env, seconds, 1)
    city = snapshot['destination']
    records = {r['name']: r for r in facts.snapshot(city, limit=60)['candidates']['attraction']
               if r['planning_catalog'] and r['routable_in_snapshot']}
    records.update((r['name'], r) for r in snapshot['candidates']['attraction'])
    spots = {row['name']: row for row in traveler.spots}
    used = {a['position'] for d in plan['itinerary'] for a in d['activities'] if a['type'] == 'attraction'}
    preferred = set(request.get('preferred_attractions', ()))
    preferred.update(r['name'] for exp in snapshot.get('experience_matches', {}).values() for r in exp['records'])
    satisfied_ids = {g['requirement_id'] for g in old['goals'] if g['status'] == 'matched'}
    evening = {}
    for goal in snapshot['requirements']:
        if goal.get('preferred_start') and goal['id'] not in satisfied_ids:
            for r in snapshot.get('experience_matches', {}).get(goal['id'], {}).get('records', ()):
                records[r['name']] = r
                evening[r['name']] = (goal['id'], minutes(goal['preferred_start']))
    intents = request['profile']['interest_types']
    day_limit = min(3 if pace == 'full' else 2, request['profile']['hard_limits'].get('max_daily_attractions', 3))
    remaining_money = request.get('budget', float('inf')) - old['quality_vector']['total_cost_cny']
    # Try one nearby experience per day. Keep a break before the next existing stop.
    try:
        for day in plan['itinerary']:
            activities = day['activities']
            visits = [a for a in activities if a['type'] == 'attraction']
            hard_limit = request['profile']['hard_limits'].get('max_daily_attractions', 3)
            if len(visits) >= min(hard_limit, day_limit + bool(evening)):
                continue
            best = None
            for i in range(1, len(activities)):
                prev, nxt = activities[i-1:i+1]
                origin = prev.get('position') or prev['end']
                target = nxt.get('position') or nxt['start']
                start_at = minutes(prev['end_time'])
                buffer = {'train': 60, 'airplane': 120}.get(nxt['type'], 0)
                deadline = minutes(nxt['start_time']) - buffer - 30
                evening_slot = bool(evening and prev['type']=='dinner' and nxt['type']=='accommodation')
                if evening_slot:
                    deadline = 21 * 60 + 30
                elif len(visits) >= day_limit:
                    continue
                if deadline - start_at - _route_minutes(nxt['transports']) < 60:
                    continue
                def proximity(name):
                    return traveler.distance(origin, name) + traveler.distance(name, target)
                options = [name for name in records if name in spots and name not in used and name in traveler.points]
                if evening_slot:
                    options = [n for n in options if n in evening]
                options.sort(key=proximity)
                for name in options[:10]:
                    traveler.check()
                    rec = records[name]
                    stay = rec['recommended_stay_hours']
                    duration = max(60, round(stay[0] * 60)) if stay else 90
                    suggested = request.get('experience_earliest', {}).get(name)
                    for lo, hi in rec['opening']['windows_minutes']:
                        lo = max(lo, minutes(suggested) if suggested else 480, evening.get(name, ('', 0))[1])
                        latest = min(deadline, hi) - duration
                        if max(lo, start_at) > latest:
                            continue
                        for _, arrived, incoming in traveler.routes_to(origin, name, start_at, latest, 'local'):
                            begin = max(lo, arrived)
                            if begin > latest:
                                continue
                            end = begin + duration
                            visit = traveler.poi_activity(spots[name], 'attraction', begin, end, incoming)
                            for _, reached, outgoing in traveler.routes_to(name, target, end, deadline, 'local'):
                                legs = incoming + outgoing
                                original = nxt['transports']
                                travel = _route_minutes(legs) - _route_minutes(original)
                                walk = _route_minutes(legs, 'walk') - _route_minutes(original, 'walk')
                                cost = total_cost([visit]) + sum(t['cost'] for t in outgoing) - sum(t['cost'] for t in original)
                                requested = name in evening
                                if (travel > (60 if requested else 15) or walk > (20 if requested else 12)
                                        or cost > min(remaining_money, (150 if requested else 60) * snapshot['people'])):
                                    continue
                                tags = rec['tags']['values']
                                match = name in preferred or any(term in tag for term in intents for tag in tags)
                                key = (not requested, not match, max(0, travel), max(0, walk), cost, proximity(name), name)
                                if best is None or key < best[0]:
                                    best = (key, i, visit, outgoing, cost, reached if evening_slot else None)
            if best:
                _, i, visit, onward, cost, checkin = best
                activities[i] = {**activities[i], 'transports': onward}
                if checkin is not None:
                    activities[i]['start_time'] = clock(checkin)
                activities.insert(i, visit)
                used.add(visit['position'])
                if visit['position'] in evening:
                    hit, _ = evening[visit['position']]
                    evening = {n: entry for n, entry in evening.items() if entry[0] != hit}
                remaining_money -= cost
                log['changes'].append({'day': day['day'], 'place': visit['position']})
    except SearchBudgetExpired:
        log['deadline_reached'] = True
    log['route_calls'] = traveler.route_calls
    if not log['changes']:
        return finish('no_suitable_gap')
    variant = {**parent, 'id': _digest(plan), 'itinerary': plan, 'visit_refined_from': parent_id}
    if any(c['id'] == variant['id'] for c in archive['archive']):
        return finish('already_in_archive')
    checked = evaluate_archive(spec, snapshot, {**archive, 'archive': [variant]}, env, facts)['candidates'][0]
    if not checked['validation_report']['passed']:
        log['issues'] = checked['validation_report']['issues']
        return finish('rejected_by_verification')
    def satisfied(c):
        return {g['requirement_id'] for g in c['goals'] if g['status'] == 'matched'}
    if not satisfied(old) <= satisfied(checked):
        return finish('lost_preference')
    variant.update(validation=checked['validation_report'], quality=checked['quality_vector'], places=checked['places'])
    archive = {**archive, 'archive': [*archive['archive'], variant]}
    evaluated = [*assessment['candidates'], checked]
    eligible = [c for c in evaluated if c['validation_report']['passed']]
    assessment = {**assessment, 'candidates': evaluated, 'verified_count': len(eligible),
                  'candidate_count': len(evaluated), 'selection': select_candidates(eligible)}
    return finish('verified_variant')
