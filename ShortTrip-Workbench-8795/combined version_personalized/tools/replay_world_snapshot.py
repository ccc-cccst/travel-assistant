"""Save trip-aware V20 snapshots from the original P009 inputs."""

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.world_facts import WorldFacts


P009 = ROOT / 'result/experiments/P009_stable_planning'
REQUESTS = {
    'human_69': P009 / 'constraints_run_04/human_69/request.json',
    'medium_140': P009 / 'constraints_run_04/medium_140/request.json',
    'four_day_appointment': P009 / 'run_06/four_day_appointment/request.json',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--limit', type=int, default=12)
    parser.add_argument('--case', action='append', choices=tuple(REQUESTS))
    args = parser.parse_args()
    out = fresh(args.out)
    start = perf_counter()
    environment(args.data)
    facts = WorldFacts.configured(args.data)
    setup = round(perf_counter() - start, 3)
    cases = []
    for case in dict.fromkeys(args.case or REQUESTS):
        path = REQUESTS[case]
        spec = read(path)
        save(out / case / 'request.json', spec)
        start = perf_counter()
        snapshot = facts.trip_snapshot(spec, limit=args.limit)
        elapsed = round(perf_counter() - start, 3)
        save(out / case / 'WORLD_FACTS.json', snapshot)
        row = {
            'case': case, 'input': path.relative_to(ROOT).as_posix(), 'snapshot_id': snapshot['snapshot_id'],
            'snapshot_seconds': elapsed, 'counts': snapshot['coverage'],
            'transport': {key: group[0]['counts'] for key, group in snapshot['intercity'].items()},
            'places': [{'query': g.get('query'), 'status': g['status'],
                        'records': [p['name'] for p in g['records']], 'visit': g.get('visit')}
                       for g in snapshot['place_groundings']],
            'unapplied_fact_filters': snapshot['unapplied_fact_filters'],
        }
        cases.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    save(out / 'SUMMARY.json', {'data': facts.version, 'initialization_seconds': setup,
                               'limit': args.limit, 'model_calls': 0, 'cases': cases})


if __name__ == '__main__':
    main()
