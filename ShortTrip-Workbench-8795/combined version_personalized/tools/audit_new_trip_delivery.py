"""Freeze unused benchmark requests and replay them through the product HTTP entry."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from getpass import getpass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from time import perf_counter, sleep
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import ROOT, fresh, read, save


P034 = ROOT/'result/experiments/P034_delivery_and_quality'
CORPUS = ROOT.parent/'datasets/ChinaTravel'
LEVELS = ('easy', 'medium', 'human')


def text_family(text):
    return re.sub(r'\W+', '', re.sub(r'^\s*[\[【][^\]】]+[\]】]', '', text)).strip()


def freeze():
    path = P034/'FROZEN30.json'
    if path.exists():
        return read(path)
    result = ROOT/'result/experiments'
    p007 = result/'P007_tripspec_evaluation/GOLD_DRAFT.jsonl'
    seen_rows = [json.loads(line) for line in p007.read_text(encoding='utf8').splitlines()]
    used = {text_family(row['raw_text']) for row in seen_rows}
    previous = set()
    for request in result.rglob('request.json'):
        if P034.name in request.parts:
            continue
        for name in request.parts:
            if match := re.match(r'^(easy|medium|human)_(\d+)(?!\d)', name):
                previous.add(f'{match[1]}_{int(match[2])}')
    cases, hashes = [], {}
    for level in LEVELS:
        source = CORPUS/(level+'.csv')
        hashes[level] = sha256(source.read_bytes()).hexdigest()
        with source.open(encoding='utf-8-sig', newline='') as stream:
            records = []
            for i, row in enumerate(csv.DictReader(stream), 1):
                raw = row['nature_language']
                family = text_family(raw)
                case = f'{level}_{i}'
                if family and case not in previous:
                    records.append((sha256(('P034-frozen-v1/'+row['uid']).encode()).hexdigest(),
                                    case, family, raw, row))
        chosen = 0
        for _, case, family, raw, row in sorted(records):
            if family in used:
                continue
            used.add(family)
            cases.append({'case': case, 'level': level, 'uid': row['uid'], 'raw_text': raw,
                          'raw_sha256': sha256(raw.encode()).hexdigest(),
                          'corpus_core': {'origin_city': row['start_city'], 'destination_city': row['target_city'],
                                          'people': int(row['people_number']), 'days': int(row['days'])}})
            chosen += 1
            if chosen == 10:
                break
        if chosen != 10:
            raise RuntimeError(f'Only {chosen} unused {level} requests')
    return save(path, {'source_sha256': hashes, 'cases': cases,
                       'selection': 'stable hash order, distinct raw texts, omit P007 and previous planner cases'})


def run_http(cases, output, *, api_key, workers, followup=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    env = dict(os.environ)
    if api_key:
        env['DEEPSEEK_API_KEY'] = api_key
    else:
        env.pop('DEEPSEEK_API_KEY', None)
    log = (output/'SERVER.log').open('w', encoding='utf8')
    server = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', '-m', 'chinatravel.personalization.web',
                               '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)

    def send(path, payload=None):
        request = Request(origin+path, data=None if payload is None else json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=65) as response:
            return json.load(response)

    def evaluate(case):
        case_id = case['case']
        folder = output/case_id
        start = perf_counter()
        spec = send('/tripspec/interpret', {'raw_text': case['raw_text'], 'form': {}})
        interpreted = perf_counter() - start
        save(folder/'TRIPSPEC.json', spec)
        core = {a['field']: a['value'] for a in spec['items'] if a['field'] in case['corpus_core']}
        changed = {k: [case['corpus_core'][k], core.get(k)] for k in case['corpus_core']
                   if case['corpus_core'][k] != core.get(k)}
        payload={k: spec[k] for k in ('request_id', 'request_revision')}
        job = send('/trip-planning', {**payload,'try_budget_alternatives':followup})
        first_complete = None
        for _ in range(300):
            status = send('/trip-planning/job?id='+job['id'])
            recovery = status.get('recovery', {})
            if first_complete is None and (status.get('first') or status.get('result')
                                           or any(a.get('view') for a in recovery.get('attempts', []))):
                first_complete = perf_counter() - start
            if status['state'] not in ('starting', 'planning'):
                break
            sleep(.45)
        else:
            status = {**status, 'state': 'client_timeout'}
        save(folder/'WEB_RESULT.json', status)
        job_folder = ROOT/'result/manual'/job['id']
        for name in ('STATUS.json', 'request.json', 'PLAN_VIEW.json', 'CANDIDATE_ARCHIVE.json',
                     'PLAN_ASSESSMENT.json', 'RECOVERY.json', 'TRIP_SKETCHES.json', 'REQUIREMENT_REPORT.json'):
            source = job_folder/name
            if source.is_file():
                save(folder/name, read(source))
        outcome = ('original' if status.get('result') else 'conditional' if first_complete is not None
                   else 'city_draft' if recovery.get('draft') else 'empty')
        if outcome == 'original' and status.get('delivery_status') == 'core_experience_pending':
            outcome = 'core_experience_pending'
        row = {'case': case_id, 'level': case['level'], 'job': job['id'], 'ready': spec['ready'],
               'issues': [q['code'] for q in spec['issues']], 'core_differences': changed,
               'interpret_seconds': interpreted, 'interpret_model': spec['model'], 'sketch_model': status.get('model'),
               'state': status['state'], 'outcome': outcome, 'first_complete_seconds': first_complete,
               'elapsed_seconds': perf_counter()-start}
        if followup:
            answers={}
            for issue in spec['issues']:
                field=issue['field']
                options=issue.get('choices',[])
                indices=[idx for idx,opt in enumerate(options) if field in case['corpus_core']
                         and opt['value']==case['corpus_core'][field]]
                if len(indices)==1:
                    answers[issue['id']]=indices[0]
            preview=next((a for a in recovery.get('attempts',[]) if a.get('view')),None)
            if not status.get('result') and preview:
                revised=send('/trip-planning/recovery/accept',{'job_id':job['id'],'attempt_id':preview['id']})
                accepted=send('/trip-planning/job?id='+job['id'])
                row['simulated_followup']={'action':'accept_preview','complete':bool(accepted.get('result')),
                                          'revision':revised['request_revision'],'change':preview['change']}
            elif not status.get('result') and answers and len(answers)==len(spec['issues']):
                revised=send('/tripspec/resolve',{**payload,'choices':answers})
                nextjob=send('/trip-planning',{k:revised[k] for k in payload})
                for _ in range(300):
                    resumed=send('/trip-planning/job?id='+nextjob['id'])
                    if resumed['state'] not in ('starting','planning'):
                        break
                    sleep(.45)
                save(folder/'SIMULATED_RESULT.json',resumed)
                row['simulated_followup']={'action':'choose_benchmark_core','complete':bool(resumed.get('result')),
                                          'choices':answers,'state':resumed['state']}
            row['elapsed_including_simulated_seconds']=perf_counter()-start
        save(folder/'MEASUREMENT.json', row)
        print(case_id, outcome, status['state'], flush=True)
        return row

    try:
        for _ in range(120):
            try:
                send('/options')
                break
            except URLError:
                if server.poll() is not None:
                    raise RuntimeError('ShortTrip web server exited')
                sleep(.25)
        else:
            raise RuntimeError('ShortTrip web server did not become ready')
        def measured(case):
            if not followup:
                return evaluate(case)
            tick=perf_counter()
            try:
                return evaluate(case)
            except Exception as error:
                row={'case':case['case'],'level':case['level'],'state':'measurement_error','outcome':'empty',
                     'error_type':type(error).__name__,'elapsed_seconds':perf_counter()-tick}
                save(output/case['case']/'MEASUREMENT.json',row)
                return row
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for _ in executor.map(measured, cases):
                pass
        rows = [read(output/r['case']/'MEASUREMENT.json') for r in cases]
        save(output/'SUMMARY.json', {'model_configured': bool(api_key), 'rows': rows})
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-only', action='store_true')
    parser.add_argument('--ask-key', action='store_true')
    parser.add_argument('--out', type=Path, default=P034/'web_live_01')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    cases = freeze()['cases']
    if args.freeze_only:
        print(f'{len(cases)} frozen')
        return
    key = getpass('DeepSeek API key (hidden): ') if args.ask_key else os.environ.get('DEEPSEEK_API_KEY')
    run_http(cases[:args.limit], fresh(args.out), api_key=key, workers=args.workers)


if __name__ == '__main__':
    main()
