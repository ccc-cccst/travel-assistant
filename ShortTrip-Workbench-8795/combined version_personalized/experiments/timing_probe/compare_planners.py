"""Serial three-planner comparison with a common budget and candidate target."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=1800)
    parser.add_argument('--candidate-limit', type=int, default=10)
    parser.add_argument('--provider', choices=('deepseek', 'qwen'), default='deepseek')
    parser.add_argument('--endpoint', default='http://127.0.0.1:18080/v1')
    parser.add_argument('--cases', nargs='+', default=['easy_1', 'easy_2', 'easy_3'])
    args = parser.parse_args()
    if args.candidate_limit < 1:
        parser.error('--candidate-limit must be positive')
    if args.provider == 'deepseek' and not os.environ.get('DEEPSEEK_API_KEY'):
        parser.error('Set DEEPSEEK_API_KEY in the backend environment.')
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        temp = args.out / (name + '.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(args.out / name)
    versions = ('baseline', 'day_sets', 'combo_schedule')
    files = [p for folder in ('chinatravel', 'experiments/c_only', 'experiments/timing_probe')
             for p in (ROOT / folder).rglob('*.py') if 'database' not in p.parts and '__pycache__' not in p.parts]
    files.extend(ROOT / 'experiments/c_only/queries' / (case + '.json') for case in args.cases)
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    save('MANIFEST.json', {'date': '2026-11-19', 'versions': versions, 'provider': args.provider,
                          'seconds': args.seconds, 'candidate_limit': args.candidate_limit, 'time_policy': 'flexible',
                          'cases': args.cases, 'repeats_per_cell': 1, 'source_sha256': hashes})
    results = {}
    for index, case in enumerate(args.cases):
        for planner in versions[index % 3:] + versions[:index % 3]:
            label = case + '__' + planner
            save('STATUS.json', {'finished': False, 'job': label, 'completed': len(results), 'total': len(args.cases) * 3})
            print('START ' + label, flush=True)
            out = args.out / label
            cmd = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).with_name('run_planner.py')),
                   '--planner', planner, '--provider', args.provider, '--endpoint', args.endpoint,
                   '--query', str(ROOT / 'experiments/c_only/queries' / (case + '.json')),
                   '--out', str(out), '--seconds', str(args.seconds), '--candidate-limit', str(args.candidate_limit)]
            started = time.perf_counter()
            try:
                with (args.out / (label + '.log')).open('x', encoding='utf-8') as log:
                    worker = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                            timeout=args.seconds + 90)
                row = json.loads((out / 'comparison.json').read_text(encoding='utf-8')) if worker.returncode == 0 else {'process_error': worker.returncode}
            except subprocess.TimeoutExpired:
                row = {'process_error': 'outer_watchdog'}
            row['process_seconds'] = time.perf_counter() - started
            results[label] = row
            save('RESULTS.json', results)
            print('DONE ' + label + ' ' + str(row.get('first_independent_legal_seconds')) +
                  ' legal=' + str(row.get('selected_legal')), flush=True)
    changed = [p for p, h in hashes.items() if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h]
    save('STATUS.json', {'finished': True, 'completed': len(results), 'source_changes': changed})
    if changed:
        raise RuntimeError('Sources changed during collection')


if __name__ == '__main__':
    main()
