"""Run a fixed, non-Oracle ChinaTravel preview in isolated processes.

The optional wall limit bounds this preview, not a paper benchmark setting.
The API key is inherited through the environment and never serialized.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'deepseek-flash'
VERSION = 'DeepSeek-V4.1-Flash official API; non-thinking; checked 2026-10-17'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=3, choices=[1, 2, 3, 4])
    parser.add_argument('--wall-timeout', type=int, default=900)
    parser.add_argument('--suite-folder', type=Path, default=ROOT / 'reproduction' / 'flash_preview_20261017')
    parser.add_argument('--split', default='preview5_local')
    args = parser.parse_args()
    if args.wall_timeout <= 0:
        parser.error('--wall-timeout must be positive')
    if not (os.environ.get('CHINATRAVEL_OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY')):
        parser.error('API key is required in the process environment')

    folder = args.suite_folder.resolve()
    if not folder.is_relative_to(ROOT):
        parser.error('--suite-folder must be inside this workspace')
    folder.mkdir(exist_ok=True)
    suite = json.loads((folder / 'selection.json').read_text(encoding='utf-8'))
    status = {
        'model': MODEL, 'provider_declared_version': VERSION,
        'split': args.split, 'oracle_translation': False,
        'workers': args.workers, 'per_query_wall_timeout_seconds': args.wall_timeout,
        'note': 'Fixed exploratory preview; wall-time interruptions are not completed benchmark results.',
        'cases': [{**case, 'status': 'queued'} for case in suite['cases']],
    }
    lock = threading.Lock()

    def update(index, **values):
        with lock:
            status['cases'][index].update(values)
            status['updated_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            pending = folder / 'status.tmp'
            pending.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            pending.replace(folder / 'status.json')

    def run_case(index):
        uid = status['cases'][index]['uid']
        logfile = folder / (uid + '.log')
        started = time.monotonic()
        update(index, status='running', started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), log=str(logfile))
        print('START', uid, flush=True)
        command = [sys.executable, '-X', 'utf8', '-u', str(ROOT / 'reproduction' / 'run.py'),
                   '--model', MODEL, '--base-url', 'https://api.deepseek.com',
                   '--model-version', VERSION, '--split', args.split, '--index', uid]
        try:
            with logfile.open('w', encoding='utf-8') as output:
                result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                        timeout=args.wall_timeout)
            update(index, status='completed' if result.returncode == 0 else 'error', exit_code=result.returncode)
        except subprocess.TimeoutExpired:
            update(index, status='wall_timeout', exit_code=None)
        except Exception as exc:
            update(index, status='launcher_error', error_type=type(exc).__name__)
        finally:
            update(index, wall_seconds=round(time.monotonic() - started, 2),
                   ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        print('END', uid, status['cases'][index]['status'], status['cases'][index]['wall_seconds'], flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run_case, range(len(status['cases']))))
    print('Preview complete. Status:', folder / 'status.json', flush=True)


if __name__ == '__main__':
    main()
