"""Replay the three fixed layouts from P010 and P011 and check a day's order."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.lessons import record_failure
from chinatravel.personalization.sketch_planning import optimize_day, plan_from_sketches
from chinatravel.personalization.world_facts import WorldFacts


P010 = ROOT / 'result/experiments/P010_trip_world_snapshot/run_02'
P011 = ROOT / 'result/experiments/P011_trip_sketches'
CASES = ('human_69', 'medium_140', 'four_day_appointment')


def check_route(plan, optimized):
    day, result = optimized['day'], optimized['itinerary']
    before, after = plan['itinerary'][day - 1], result['itinerary'][day - 1]

    def identity(rows):
        return Counter((a['type'], a.get('position'), a.get('TrainID', a.get('FlightID')), a.get('start'), a.get('end'))
                       for a in rows)

    assert identity(before['activities']) == identity(after['activities'])
    assert all(a == b for a, b in zip(plan['itinerary'], result['itinerary']) if a['day'] != day)
    distance = optimized['distance']
    if optimized['state'] == 'improved':
        assert distance['after_km'] < distance['before_km']
    else:
        assert plan == result and distance['before_km'] == distance['after_km']
    assert optimized['validation']['passed'] and optimized['model_calls'] == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--seconds', type=float, default=24)
    args = parser.parse_args()
    out = fresh(args.out)
    begin = perf_counter()
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    setup = round(perf_counter() - begin, 4)
    rows = []
    for case in dict.fromkeys(args.case or CASES):
        inputs = P010 / case
        layouts_path = P011 / ('live_04' if case == 'four_day_appointment' else 'run_04') / case / 'TRIP_SKETCHES.json'
        spec, world, layouts = read(inputs / 'request.json'), read(inputs / 'WORLD_FACTS.json'), read(layouts_path)
        folder = out / case
        save(folder / 'request.json', spec)
        result = plan_from_sketches(spec, world, layouts, env, facts, max_seconds=args.seconds,
                                   on_first=lambda entry: save(folder / 'FIRST_CANDIDATE.json', entry))
        save(folder / 'CANDIDATE_ARCHIVE.json', result)
        archive = result.get('archive', [])
        if not archive:
            code = 'search_exhausted' if result['state'] == 'no_verified_candidate' else result['state']
            record_failure(folder, {**result, 'reason_code': code}, spec)
        optimizations = []
        for candidate in archive[:2]:
            plan = candidate['itinerary']
            day = max(plan['itinerary'], key=lambda d: sum(a['type'] == 'attraction' for a in d['activities']))
            optimized = optimize_day(spec, world, plan, day['day'], env, facts)
            check_route(plan, optimized)
            optimizations.append({'candidate_id': candidate['id'], **optimized})
        save(folder / 'ROUTE_OPTIMIZATIONS.json', optimizations)
        row = {'case': case, 'state': result['state'], 'stop_reason': result.get('stop_reason'),
               'first_verified_seconds': result.get('first_verified_seconds'),
               'planning_seconds': result.get('planning_seconds'), 'route_calls': result.get('route_calls'),
               'preparation_seconds': result.get('preparation_seconds'), 'search_seconds': result.get('search_seconds'),
               'verification_seconds': result.get('verification_seconds'),
               'archive': dict(Counter(c['source'] for c in archive)),
               'trials': [{'source': d['source'], 'reasons': d['reasons']} for d in result.get('diagnostics', [])],
               'route_optimization': [r['state'] for r in optimizations], 'model_calls': 0}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    save(out / 'SUMMARY.json', {'initialization_seconds': setup, 'search_seconds_per_case': args.seconds, 'cases': rows})


if __name__ == '__main__':
    main()
