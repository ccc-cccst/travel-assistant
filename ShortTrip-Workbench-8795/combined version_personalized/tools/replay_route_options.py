"""Check distance-based routes on the frozen recommendations and three transport examples."""

import argparse
from collections import Counter
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay_integrated_planning import RESULTS, select_cases
from replay_sketch_planning import check_route
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_route
from chinatravel.personalization.plan_assessment import evaluate_archive
from chinatravel.personalization.sketch_planning import optimize_day
from chinatravel.personalization.world_facts import WorldFacts


P016 = RESULTS / 'P016_meal_refinement/paired_02'
APPOINTMENT = RESULTS / 'P014_integrated_planning/live_appointment'
EXAMPLES = {'medium_104': (P016 / 'medium_104/refined', 1, {'walk'}),
            'easy_61': (P016 / 'easy_61/refined', 1, {'metro', 'taxi', 'walk'}),
            'four_day_appointment': (APPOINTMENT, 3, {'metro', 'taxi', 'walk'})}


def recommendation(folder):
    assessed = read(folder / 'PLAN_ASSESSMENT.json')
    return assessed['selection']['recommended_id']


def replay_case(folder, out, env, facts):
    spec, snapshot = read(folder / 'request.json'), read(folder / 'WORLD_FACTS.json')
    archive = read(folder / 'CANDIDATE_ARCHIVE.json')
    pick = recommendation(folder)
    candidate = next(c for c in archive['archive'] if c['id'] == pick)
    plan = candidate['itinerary']
    rows = []
    for day in range(1, len(plan['itinerary']) + 1):
        started = perf_counter()
        route = optimize_day(spec, snapshot, plan, day, env, facts)
        elapsed = perf_counter() - started
        check_route(plan, route)
        if route['state'] == 'improved':
            entry = {**candidate, 'itinerary': route['itinerary']}
            checked = evaluate_archive(spec, snapshot, {**archive, 'archive': [entry]}, env, facts)
            assert checked['verified_count'] == 1, checked['candidates'][0]['validation_report']['issues']
            save(out / f'DAY_{day}_IMPROVED.json', {**route, 'assessment': checked})
        metrics = ('city_transit_minutes', 'access_walk_minutes', 'total_cost_cny')
        rows.append({**{k: route[k] for k in ('day', 'state', 'distance', 'route_calls', 'stop_reason',
                                            'model_calls', 'rejections', 'trials')},
                     'before': {k: route['before'][k] for k in metrics},
                     'after': {k: route['after'][k] for k in metrics}, 'elapsed_seconds': elapsed})
    save(out / 'DAYS.json', rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = fresh(args.out)
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    days, cases, examples = [], {}, {}
    for case in select_cases('frozen30'):
        folder = P016 / case / 'refined'
        state = read(folder / 'STATUS.json')['state']
        cases[case] = state
        if state == 'complete':
            rows = replay_case(folder, out / case, env, facts)
            days.extend({'case': case, **row} for row in rows)
            print(case, dict(Counter(row['state'] for row in rows)), flush=True)

    for case, (folder, day, allowed) in EXAMPLES.items():
        seen = set()
        views = {}
        for name, view_only in (('original', True), ('optimized', False)):
            target = out / 'examples' / case / name
            status = run_trip_route(folder, target, recommendation(folder), day, env, facts, view_only=view_only)
            assert status['state'] == 'complete', status
            result = read(target / 'ROUTE_VIEW.json')
            assert result['model_calls'] == 0 and result['segments']
            if view_only:
                assert result['result'] == 'viewed'
            for segment in result['segments']:
                assert {o['mode'] for o in segment['options']} == {'metro', 'taxi', 'walk'}
                assert {o['mode'] for o in segment['options'] if o['status'] == 'restricted'} == {
                    'metro', 'taxi', 'walk'} - allowed
                seen.update(o['mode'] for o in segment['options'] if o['status'] == 'estimated')
            if case == 'four_day_appointment':
                assert any(a.get('locked') and a['name'] == '上海博物馆' and a['time'] == '15:00–16:00'
                           for a in result['activities'])
            views[name] = {k: result[k] for k in ('elapsed_seconds', 'result', 'distance')}
        assert seen == allowed, (case, seen)
        examples[case] = {'modes_with_estimates': sorted(seen), **views}

    rejections = Counter()
    for day in days:
        rejections.update(day['rejections'])
    save(out / 'SUMMARY.json', {'case_states': dict(Counter(cases.values())), 'cases': cases,
                                'day_states': dict(Counter(d['state'] for d in days)),
                                'median_button_worker_seconds': median(d['elapsed_seconds'] for d in days),
                                'max_button_worker_seconds': max(d['elapsed_seconds'] for d in days),
                                'model_calls': sum(d['model_calls'] for d in days),
                                'route_rejections': dict(rejections),
                                'examples': examples, 'days': days})


if __name__ == '__main__':
    main()
