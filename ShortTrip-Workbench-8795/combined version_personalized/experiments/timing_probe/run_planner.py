"""Collect a bounded candidate pool with one CLI for all three planners."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def command(args):
    folder = Path(__file__).resolve().parent
    shared = ['--query', str(args.query), '--out', str(args.out), '--arm', args.planner,
              '--time-policy', args.time_policy, '--seconds', str(args.seconds),
              '--candidate-limit', str(args.candidate_limit)]
    if args.provider == 'deepseek':
        return [sys.executable, '-B', '-X', 'utf8', str(folder / 'api_speed.py'), 'full', *shared]
    return [sys.executable, '-B', '-X', 'utf8', str(folder / 'run_activity_granularity.py'),
            'worker', '--endpoint', args.endpoint, *shared]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--planner', choices=('baseline', 'day_sets', 'combo_schedule'), required=True)
    parser.add_argument('--provider', choices=('qwen', 'deepseek'), default='deepseek')
    parser.add_argument('--query', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data', type=Path, default=ROOT.parent / 'ShortTrip/artifacts/data/5e0ee8a')
    parser.add_argument('--seconds', type=float, default=1800)
    parser.add_argument('--candidate-limit', type=int, default=10)
    parser.add_argument('--time-policy', choices=('strict', 'flexible'), default='flexible')
    parser.add_argument('--endpoint', default='http://127.0.0.1:18080/v1')
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    if args.candidate_limit < 1:
        parser.error('--candidate-limit must be positive')
    if args.provider == 'deepseek' and not os.environ.get('DEEPSEEK_API_KEY'):
        parser.error('Set DEEPSEEK_API_KEY in the backend process environment.')
    args.query, args.out = args.query.resolve(), args.out.resolve()
    from chinatravel.environment.sandbox.__main__ import configuration
    env = {**os.environ, **configuration(args.data), 'CHINATRAVEL_TRANSLATION_MODE': 'chinatravel'}
    result = subprocess.run(command(args), cwd=ROOT, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
    if args.provider == 'qwen':
        os.environ.update(env)
        from experiments.timing_probe.run_activity_granularity import audit, read
        audit(args.out, read(args.query), args.time_policy)
    print(json.dumps({'planner': args.planner, 'out': str(args.out),
                      'budget_seconds': args.seconds, 'candidate_target': args.candidate_limit}, ensure_ascii=False))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
