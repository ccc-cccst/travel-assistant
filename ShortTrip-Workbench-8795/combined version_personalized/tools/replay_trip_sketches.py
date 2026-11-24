"""Generate trip sketches on the saved P010 snapshots."""

import argparse
from collections import Counter
from functools import partial
import getpass
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chinatravel.personalization.cli import ROOT, fresh, read, save
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.tripspec import request_deepseek_json


P010 = ROOT / 'result/experiments/P010_trip_world_snapshot/run_02'
CASES = ('human_69', 'medium_140', 'four_day_appointment')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--with-model', action='store_true')
    parser.add_argument('--ask-key', action='store_true', help='Read the DeepSeek key without echoing it')
    args = parser.parse_args()
    if args.ask_key and not args.with_model:
        parser.error('--ask-key requires --with-model')
    out = fresh(args.out)
    provider = partial(request_deepseek_json, capture_invalid=True)
    if args.ask_key:
        key = getpass.getpass('DeepSeek key: ') if sys.stdin.isatty() else sys.stdin.readline().strip()
        if not key:
            parser.error('No DeepSeek key was entered')
        provider = partial(provider, api_key=key)
    summaries = []
    for case in dict.fromkeys(args.case or CASES):
        path = P010 / case / 'WORLD_FACTS.json'
        start = perf_counter()
        result = propose_trip_sketches(read(path), use_model=args.with_model, provider=provider)
        elapsed = round(perf_counter() - start, 5)
        save(out / case / 'TRIP_SKETCHES.json', result)
        row = {'case': case, 'snapshot': path.relative_to(ROOT).as_posix(),
               'snapshot_id': result['snapshot_id'], 'state': result['state'],
               'counts': dict(Counter(s['source'] for s in result['sketches'])),
               'elapsed_seconds': elapsed,
               'model': {k: v for k, v in result['model'].items() if k not in ('draft', 'invalid_content')}}
        summaries.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    save(out / 'SUMMARY.json', {'with_model': args.with_model, 'cases': summaries})


if __name__ == '__main__':
    main()
