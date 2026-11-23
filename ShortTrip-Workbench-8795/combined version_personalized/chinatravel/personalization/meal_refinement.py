"""Replace meals within a verified itinerary's existing activity windows."""

from copy import deepcopy
from heapq import nsmallest
from time import perf_counter

from .plan_assessment import COMPARISONS, DEFAULT_TRANSFER_TOLERANCE_MINUTES, evaluate_archive, select_candidates
from .planning_compat import constructor_input
from .sketch_planning import _digest
from chinatravel.planning.engine import Constructor, SearchBudgetExpired, clock, minutes, total_cost
from chinatravel.planning.validation import MEAL_WINDOWS


ROUTING_SECONDS = 3
POOL_SIZE = 8
POSITIVE = {'prefer', 'prioritize', 'select', 'eq', 'include', 'accept'}
SNACK_OR_DRINK = {'', 'unknown', '其他', 'other', '小吃', '特色小吃', 'snacks', '面包甜点',
                  'bakery and desserts', '甜品店', '蛋糕店', '蛋糕甜品店', '糕点/烘焙', '面包',
                  '西式糕点', '中式糕点', '冰淇淋', '糖水', '烧仙草', '酸奶', '饮品', '冷饮店',
                  '奶茶/茶饮', '咖啡', '咖啡店', '咖啡厅', 'coffee shop', '茶馆/茶室', '茶座',
                  '茶艺馆', 'teahouse', '酒吧/酒馆', '酒吧', 'bar/pub', '啤酒屋', '清吧', '红酒吧'}


def _meal_goals(snapshot, evaluated):
    grounded = {g['requirement_id']: {r['name'] for r in g['records']}
                for g in snapshot['place_groundings'] if g['status'] == 'found'}
    result = {}
    for goal in evaluated['goals']:
        if goal['operator'] not in POSITIVE or goal['status'] not in {'matched', 'unmet'}:
            continue
        value = goal['value']
        values = value if isinstance(value, list) else [value]
        if goal['field'] == 'restaurant' and goal['requirement_id'] in grounded:
            result[goal['requirement_id']] = ('name', grounded[goal['requirement_id']])
        elif goal['field'] == 'cuisine':
            result[goal['requirement_id']] = ('cuisine', {v.casefold() for v in values if isinstance(v, str) and v})
    return result


def _matches(row, goals):
    if row is None:
        return set()
    cuisine = str(row.get('cuisine', '')).casefold()
    return {key for key, (field, terms) in goals.items()
            if (row['name'] in terms if field == 'name' else any(term in cuisine for term in terms))}


def _route_minutes(legs, mode=None):
    return sum(minutes(t['end_time']) - minutes(t['start_time']) for t in legs
               if mode is None or t['mode'] == mode)


def _try_meal(traveler, row, kind, start, target, earliest, latest, duration, *, fixed_start=None):
    opens, last_start, closes = MEAL_WINDOWS[kind]
    opening, closing = minutes(row['opentime']), minutes(row['endtime'])
    if closing <= opening:
        closing = 1440
    cutoff = min(last_start - 1, closes - duration, closing - duration, latest - duration)
    first_start = max(earliest, opens, opening) if fixed_start is None else fixed_start
    if not max(earliest, opens, opening) <= first_start <= cutoff:
        return
    if fixed_start is not None:
        cutoff = fixed_start
    for _, arrived, incoming in traveler.routes_to(start, row['name'], earliest, cutoff, 'local'):
        begin = max(arrived, first_start)
        end = begin + duration
        for _, _, onward in traveler.routes_to(row['name'], target, end, latest, 'local'):
            yield traveler.poi_activity(row, kind, begin, end, incoming), onward


def _replacement(traveler, meal, following, start, earliest, occupied, goals, cap, detour=0):
    kind = following['type']
    target = following['start'] if kind in ('train', 'airplane') else following['position']
    latest = minutes(following['start_time']) - {'train': 60, 'airplane': 120}.get(kind, 0)
    duration = minutes(meal['end_time']) - minutes(meal['start_time'])
    legs = meal['transports'] + following['transports']
    current_minutes = _route_minutes(legs)
    current_walk = _route_minutes(legs, 'walk')
    current_cost = meal['cost'] + sum(t['cost'] for t in legs)

    def estimate(row):
        name = row['name']
        return traveler.distance(start, name) + traveler.distance(name, target)

    available = [r for r in traveler.food if r['name'] not in occupied
                 and str(r.get('cuisine', '')).strip().casefold() not in SNACK_OR_DRINK]
    affordable = [r for r in available if r['price'] <= meal['price']]
    goal_rows = [r for r in available if _matches(r, goals)] if goals else []
    pool = [*nsmallest(POOL_SIZE, affordable, key=estimate),
            *nsmallest(POOL_SIZE, affordable, key=lambda r: estimate(r) + r['price'] * .1),
            *nsmallest(POOL_SIZE, goal_rows, key=estimate)]
    best, checked = None, set()
    for row in pool:
        name = row['name']
        if name in checked:
            continue
        checked.add(name)
        hits = _matches(row, goals)
        try:
            traveler.check()
            for proposed, onward in _try_meal(traveler, row, meal['type'], start, target, earliest, latest, duration):
                new_legs = proposed['transports'] + onward
                travel_change = _route_minutes(new_legs) - current_minutes
                walk_change = _route_minutes(new_legs, 'walk') - current_walk
                cost_change = round(proposed['cost'] + sum(t['cost'] for t in new_legs) - current_cost, 2)
                if cost_change > cap + .005:
                    continue
                changes = (travel_change, walk_change, cost_change, proposed['price'] - meal['price'])
                saves_money = cost_change <= -COMPARISONS['total_cost_cny']
                strictly_better = max(changes) <= 0 and min(changes) < 0
                small_detour = max(travel_change, walk_change) <= detour
                if not strictly_better and not (small_detour and (saves_money or hits)):
                    continue
                score = (-len(hits), not saves_money, travel_change, walk_change, cost_change, proposed['price'], name)
                if best is None or score < best[0]:
                    best = (score, proposed, onward, hits)
        except SearchBudgetExpired:
            break
        except (TypeError, ValueError, KeyError):
            continue
    direct = None
    if best is not None:
        try:
            bypass = traveler.routes_to(start, target, earliest, latest, 'local')
            direct = min((_route_minutes(legs) for _, _, legs in bypass), default=None)
        except SearchBudgetExpired:
            pass
        return (*best, direct)
    return None


def refine_meals(spec, snapshot, archive, assessment, env, facts, *, seconds, max_candidates):
    """Keep the first and original recommended plans available while testing replacements."""
    start_time = perf_counter()
    entries = archive['archive']
    selected = assessment['selection']['recommended_id']
    protected = {entries[0]['id'], selected} if entries else set()
    evidence = {'changes': [], 'route_calls': 0, 'model_calls': 0, 'source_candidate_id': selected}

    def finish(state):
        return archive, assessment, {**evidence, 'state': state, 'seconds': perf_counter() - start_time}

    if seconds <= 0 or selected is None or max_candidates <= len(protected):
        return finish('no_search_capacity')
    source = next(c for c in entries if c['id'] == selected)
    before = next(c for c in assessment['candidates'] if c['id'] == selected)
    plan = deepcopy(source['itinerary'])
    request, _ = constructor_input(spec, snapshot=snapshot)
    traveler = Constructor(request, env, min(seconds, ROUTING_SECONDS), 1)
    rows = {r['name']: r for r in traveler.food}
    goals = _meal_goals(snapshot, before)
    days = plan['itinerary']
    slots = []
    for day_index, day in enumerate(days):
        activities = tuple(day['activities'])
        for index, meal in enumerate(activities[:-1]):
            if meal['type'] in MEAL_WINDOWS and (index or day_index):
                burden = _route_minutes(meal['transports'] + activities[index + 1]['transports'])
                # Spending and route minutes decide which window to inspect first.
                slots.append((-(meal['price'] + burden), day_index, index))
    detour = DEFAULT_TRANSFER_TOLERANCE_MINUTES
    for _, day_index, index in sorted(slots):
        if perf_counter() >= traveler.deadline:
            break
        day = days[day_index]
        activities = day['activities']
        meal, following = activities[index:index + 2]
        if _matches(rows.get(meal['position']), goals):
            continue
        if index:
            previous = activities[index - 1]
            start, earliest = previous.get('position', previous.get('end')), minutes(previous['end_time'])
        else:
            start = days[day_index - 1]['activities'][-1]['position']
            departure = meal['transports'][0]['start_time'] if meal['transports'] else meal['start_time']
            earliest = minutes(departure)
        occupied = {a['position'] for d in days for a in d['activities'] if a['type'] in MEAL_WINDOWS}
        matched = set().union(*(_matches(rows.get(name), goals) for name in occupied))
        missing = {key: match for key, match in goals.items() if key not in matched}
        remaining = request.get('budget', float('inf')) - sum(total_cost(d['activities']) for d in days)
        option = _replacement(traveler, meal, following, start, earliest, occupied, missing, remaining, detour)
        if option is None:
            continue
        (_, _, travel_change, walk_change, cost_change, _, _), replacement, onward, hits, direct = option
        activities[index] = replacement
        activities[index + 1] = {**following, 'transports': onward}
        detour -= max(0, travel_change, walk_change)
        via = _route_minutes(replacement['transports'] + onward)
        evidence['changes'].append({'day': day['day'], 'meal': meal['type'], 'before': meal['position'],
                                    'after': replacement['position'], 'transit_delta': travel_change,
                                    'walk_delta': walk_change, 'cost_delta': cost_change, 'matched_goals': sorted(hits),
                                    'direct_minutes': direct, 'via_meal_minutes': via})
    evidence['route_calls'] = traveler.route_calls
    if not evidence['changes']:
        return finish('no_replacement')
    candidate_id = _digest(plan)
    if any(c['id'] == candidate_id for c in entries):
        return finish('already_available')
    variant = {**source, 'id': candidate_id, 'itinerary': plan, 'meal_refined_from': selected}
    trial = evaluate_archive(spec, snapshot, {**archive, 'archive': [variant]}, env, facts)
    checked = trial['candidates'][0]
    report = checked['validation_report']
    evidence['verification_issues'] = report['issues']
    if not report['passed']:
        return finish('verification_failed')
    old_matches = {g['requirement_id'] for g in before['goals'] if g['status'] == 'matched'}
    new_matches = {g['requirement_id'] for g in checked['goals'] if g['status'] == 'matched'}
    if not old_matches <= new_matches:
        return finish('preference_regression')
    verified = [c for c in assessment['candidates'] if c['validation_report']['passed']]
    ranked = select_candidates([*verified, checked])
    visible = {ranked['recommended_id'], *(c['id'] for c in ranked['alternatives'])}
    if candidate_id not in visible:
        return finish('no_visible_improvement')

    if len(entries) >= max_candidates:
        removed = next((c['id'] for c in reversed(entries) if c['id'] not in protected | visible), None)
        if removed is None:
            return finish('no_search_capacity')
        entries = [c for c in entries if c['id'] != removed]
        assessment = {**assessment, 'candidates': [c for c in assessment['candidates'] if c['id'] != removed]}
    variant.update(validation=report, quality=checked['quality_vector'], places=checked['places'])
    entries = [*entries, variant]
    assessed = [*assessment['candidates'], checked]
    verified = [c for c in assessed if c['validation_report']['passed']]
    archive = {**archive, 'archive': entries}
    assessment = {**assessment, 'candidates': assessed, 'candidate_count': len(assessed),
                  'verified_count': len(verified), 'selection': select_candidates(verified)}
    evidence['candidate_id'] = candidate_id
    return finish('added_candidate')
