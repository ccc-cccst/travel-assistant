"""Separate experiment: freeze upstream once, compare one/two search processes."""
import argparse
import ast
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.parallel_search.pool import run_pool, save
from experiments.parallel_search.runtime import configure, make_agent, make_model, pack, worker, SearchStopped


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def core_hashes():
    paths = [p for directory in ('chinatravel', 'experiments/c_only', 'experiments/timing_probe')
             for p in (ROOT / directory).rglob('*.py') if 'database' not in p.parts and '__pycache__' not in p.parts]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def finalize_bundle(bundle, out, provenance):
    bundle['core_sha256'] = core_hashes()
    bundle['data_install_sha256'] = hashlib.sha256((Path(bundle['data']) / 'install.json').read_bytes()).hexdigest()
    bundle['provenance'] = provenance
    save(out / 'bundle.json', bundle)
    print(json.dumps({'bundle': str(out / 'bundle.json'), 'combinations': len(bundle['tasks']),
                      'scope': provenance['scope']}, ensure_ascii=False))


def prepare(args):
    """Translate/rank using the existing code, then capture before any root search."""
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise SystemExit('Set DEEPSEEK_API_KEY in this backend environment before API preparation.')
    args.out.mkdir(parents=True, exist_ok=False)
    configure(args.data)
    from chinatravel.data.load_datasets import ORACLE_FIELDS
    import experiments.timing_probe.combo_schedule as module
    deadline = time.monotonic() + args.seconds
    captured = {}
    with (args.out / 'preparation.log').open('w', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log):
        model = make_model('deepseek', [])
        agent = make_agent(model, args.out / 'cache')
        def check():
            if time.monotonic() >= deadline:
                raise SearchStopped()
        agent._deadline_check = check
        send = model._send_request
        def bounded(messages, kwargs):
            check()
            return send(messages, {**kwargs, 'timeout': min(60, deadline-time.monotonic())})
        model._send_request = bounded
        original_dispatch = module.dispatch
        def capture(roots, make_trials, check_deadline, emit):
            tasks = list(itertools.islice(roots, args.max_combinations))
            captured.update(pack(make_trials.__self__, tasks, args.data))
        module.dispatch = capture
        try:
            query = {k:v for k,v in read(args.query).items() if k not in ORACLE_FIELDS}
            agent.run(query, load_cache=False)
        finally:
            module.dispatch = original_dispatch
    if not captured:
        raise RuntimeError('Preparation did not reach combination search; inspect preparation.log.')
    finalize_bundle(captured, args.out, {'scope': 'Actual translated DSL and model-ranked root inventory; search not run.',
        'query': str(args.query.resolve()), 'max_combinations': args.max_combinations})


def from_run(args):
    """Build an offline fixture from actual admitted train roots and model contexts."""
    args.out.mkdir(parents=True, exist_ok=False)
    configure(args.data)
    source = args.run_path.resolve()
    translations = list(source.glob('cache/translation_*/*.json'))
    if len(translations) != 1:
        raise ValueError('Require exactly one actual translated-query cache.')
    query = read(translations[0])
    records = read(source / 'day_sets.json')
    with (args.out / 'preparation.log').open('w', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log):
        agent = make_agent(make_model('recorded', records), args.out / 'cache')
        agent.query = query
        agent.memory = {key: agent.collect_poi_info_all(query['target_city'], kind) for key,kind in
            (('accommodations','accommodation'), ('attractions','attraction'), ('restaurants','restaurant'))}
        from chinatravel.agent.nesy_agent.pruning import facts, local_ranking
        budgets = [value for _,(field,op,value,shape) in facts(query)
                   if field=='cost' and shape=='scalar' and isinstance(op,(ast.LtE,ast.Lt,ast.Eq))]
        agent.required_budget = min(budgets) if budgets else None
        agent.innercity_transports_ranking_from_query = local_ranking(query, ['metro','taxi','walk'])
        go_rows = agent.collect_intercity_transport(query['start_city'],query['target_city'],'train')
        back_rows = agent.collect_intercity_transport(query['target_city'],query['start_city'],'train')
        tasks, skipped, seen = [], [], set()
        for event in read(source / 'combinations.json'):
            if event['event'] != 'admit':
                continue
            hotels = agent.memory['accommodations']
            hotels = hotels[hotels['name']==event['hotel']]
            matches = []
            for _,go in go_rows[go_rows['TrainID']==event['go']].iterrows():
                for _,back in back_rows[back_rows['BeginTime']==event['return']].iterrows():
                    for _,hotel in hotels.iterrows():
                        night = float(hotel['price']) * (query['days']-1)
                        if night <= 0:
                            continue
                        rooms = (event['fixed_cost']-(float(go['Cost'])+float(back['Cost']))*query['people_number'])/night
                        if math.isfinite(rooms) and 1 <= round(rooms) <= query['people_number'] and abs(rooms-round(rooms))<1e-7:
                            matches.append({'root': {'go_transport':go,'back_transport':back,'accommodation':hotel},
                                            'rooms':round(rooms),'fixed_cost':event['fixed_cost']})
            if len(matches)!=1:
                skipped.append({'trace':event,'reason':'train root reconstruction ambiguous or unsupported','matches':len(matches)})
                continue
            task = matches[0]
            identity = (task['root']['go_transport']['TrainID'],task['root']['back_transport']['TrainID'],event['hotel'],task['rooms'])
            if identity not in seen:
                tasks.append(task)
                seen.add(identity)
            if len(tasks)>=args.max_combinations:
                break
        if not tasks:
            raise ValueError('No unambiguous recorded train roots; use API preparation instead.')
        bundle = pack(agent, tasks, args.data)
    bundle['records'] = records
    finalize_bundle(bundle, args.out, {'scope':'Offline integration fixture, not end-to-end API timing. Only exact day contexts replay.',
        'source_run':str(source), 'local_modes':'fixed metro/taxi/walk order filtered by actual DSL',
        'required_budget':'actual DSL bound or None', 'skipped_roots':skipped})


def execute(args):
    bundle = read(args.bundle)
    changed = [name for name,digest in bundle['core_sha256'].items()
               if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest]
    if changed:
        raise RuntimeError('Frozen core differs: '+', '.join(changed))
    if hashlib.sha256((Path(bundle['data'])/'install.json').read_bytes()).hexdigest()!=bundle['data_install_sha256']:
        raise RuntimeError('Data snapshot changed')
    if args.provider=='deepseek' and not os.environ.get('DEEPSEEK_API_KEY'):
        raise SystemExit('Set DEEPSEEK_API_KEY in this backend environment before API execution.')
    if args.provider=='recorded' and not bundle.get('records'):
        raise ValueError('No recorded day contexts in this bundle.')
    summary = run_pool(worker,bundle,workers=args.workers,seconds=args.seconds,out=args.out,
                       options={'provider':args.provider},stop_mode=args.stop_mode,candidate_limit=args.candidate_limit)
    configure(bundle['data'])
    from experiments.evaluation.validation import validate_plan
    if (args.out/'FIRST_PLAN.json').exists():
        result = validate_plan(read(args.out/'FIRST_PLAN.json'),bundle['query'],config={'time_policy':bundle['query'].get('time_policy','flexible')})
        save(args.out/'independent_translated_audit.json',result)
        summary['translated_independent_legal'] = result['status']=='valid'
        from experiments.timing_probe.route_view import route_view, route_markdown
        view=route_view(read(args.out/'FIRST_PLAN.json'),bundle['query'].get('appointments',()),policy=bundle['query'].get('time_policy','flexible'))
        save(args.out/'route.json',view)
        (args.out/'ROUTE.md').write_text(route_markdown(view),encoding='utf-8')
        if args.audit_query:
            gold = validate_plan(read(args.out/'FIRST_PLAN.json'),read(args.audit_query),config={'time_policy':bundle['query'].get('time_policy','flexible')})
            save(args.out/'independent_dataset_audit.json',gold)
            summary['dataset_independent_legal'] = gold['status']=='valid'
    else:
        summary['translated_independent_legal'] = False
    from experiments.parallel_search.evaluation import evaluate
    summary['collection']=evaluate(args.out,bundle,audit_query=args.audit_query)
    summary['provider'] = args.provider
    summary['bundle_sha256'] = hashlib.sha256(args.bundle.read_bytes()).hexdigest()
    summary['first_received_includes_worker_startup'] = True
    summary['experiment_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in Path(__file__).parent.glob('*.py')}
    summary['source_unchanged'] = all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in bundle['core_sha256'].items())
    save(args.out/'SUMMARY.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('prepare','from-run'):
        p=sub.add_parser(name)
        p.add_argument('--out',type=Path,required=True)
        p.add_argument('--data',type=Path,default=ROOT.parent/'ShortTrip/artifacts/data/5e0ee8a')
        p.add_argument('--max-combinations',type=int,default=16)
    sub.choices['prepare'].add_argument('--query',type=Path,required=True)
    sub.choices['prepare'].add_argument('--seconds',type=float,default=300)
    sub.choices['from-run'].add_argument('--run-path',type=Path,required=True)
    p=sub.add_parser('run')
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,choices=(1,2,4),default=2)
    p.add_argument('--seconds',type=float,default=1800)
    p.add_argument('--provider',choices=('deepseek','recorded'),default='deepseek')
    p.add_argument('--stop-mode',choices=('first','target','budget'),default='budget')
    p.add_argument('--candidate-limit',type=int,default=20)
    p.add_argument('--audit-query',type=Path)
    args=parser.parse_args()
    if getattr(args,'max_combinations',1)<1 or getattr(args,'seconds',1)<=0 or getattr(args,'candidate_limit',1)<1:
        parser.error('max-combinations, seconds and candidate-limit must be positive')
    if args.command=='run':
        execute(args)
    elif args.command=='prepare':
        prepare(args)
    else:
        from_run(args)


if __name__=='__main__':
    main()
