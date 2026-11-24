"""Replay the shared product planning entry against the frozen confirmation outputs."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.world_facts import WorldFacts


RESULTS = ROOT / 'result/experiments'
P007 = RESULTS / 'P007_tripspec_evaluation/run_v5_replay/RESULTS.jsonl'
P011_APPOINTMENT = RESULTS / 'P011_trip_sketches/live_04/four_day_appointment/TRIP_SKETCHES.json'
SMOKE = ('easy_61', 'medium_140', 'human_69', 'four_day_appointment')


def select_cases(suite):
    if suite == 'sealed30':
        from compare_planning_regression import sealed_cases
        return sealed_cases()
    rows = [json.loads(line) for line in P007.read_text(encoding='utf-8').splitlines()]
    originals = {r['case']: r['variants']['C'] for r in rows if r['cohort'] == 'holdout'}
    if suite == 'smoke':
        inputs = {case: originals[case] for case in SMOKE[:-1]}
        inputs[SMOKE[-1]] = read(RESULTS / 'P010_trip_world_snapshot/run_02/four_day_appointment/request.json')
        return inputs
    groups = ('easy', 'medium', 'human')
    ids = [case for group in groups for case in [v for v in originals if v.startswith(group + '_')][:10]]
    assert len(ids) == 30
    return {case: originals[case] for case in ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('smoke', 'frozen30', 'sealed30'), default='smoke')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--seconds', type=float, default=24)
    parser.add_argument('--case', nargs='+', help='Replay only these case IDs from the chosen suite.')
    args = parser.parse_args()
    inputs = select_cases(args.suite)
    if args.case:
        unknown = set(args.case) - inputs.keys()
        if unknown:
            parser.error('case IDs outside the chosen suite: ' + ', '.join(sorted(unknown)))
        inputs = {case: spec for case, spec in inputs.items() if case in args.case}
    out = fresh(args.out)
    save(out / 'MANIFEST.json', {
        'suite': args.suite, 'input_source': P007.relative_to(ROOT).as_posix(), 'seconds': args.seconds,
        'cases': [{'id': case, 'ready': spec['ready'],
                   'text_sha256': sha256(spec['raw_text'].encode()).hexdigest()}
                  for case, spec in inputs.items()],
    })
    start = perf_counter()
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    initialization = perf_counter() - start
    rows = []
    for case, spec in inputs.items():
        folder = out / case
        layouts = read(P011_APPOINTMENT) if case == 'four_day_appointment' else None
        status = run_trip_plan(spec, folder, env, facts, seconds=args.seconds, saved_layouts=layouts)
        row = {'id': case, 'state': status['state'], 'count': status.get('candidate_count', 0),
               'first_visible_seconds': status.get('first_visible_seconds'), 'elapsed_seconds': status['elapsed_seconds'],
               'unsupported_fields': [i['field'] for i in status.get('evidence', {}).get('unsupported_requirements', [])],
                   'model': status.get('model'), 'timings': status['timings'], 'stage': status['stage'],
                   'reason_code': status.get('reason_code'), 'error_code': status.get('error_code')}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        save(out / 'SUMMARY.json', {'initialization_seconds': initialization, 'total': len(inputs),
                                   'completed_cases': len(rows),
                                   'states': dict(Counter(r['state'] for r in rows)), 'cases': rows})


if __name__ == '__main__':
    main()
