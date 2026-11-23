"""Prepare, convert, replay or plan with the independent personalized version."""
import argparse
import ast
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time

from chinatravel.environment.sandbox.__main__ import default_installation

ROOT=Path(__file__).resolve().parents[2]
DATA=default_installation()


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.name=='STATUS.json' and value.get('state') not in ('starting','planning','complete'):
        from .lessons import record_failure,scrub
        value=scrub(value)
        request_file=path.parent/'request.json'
        request=read(request_file) if request_file.exists() else None
        diagnostic=record_failure(path.parent,value,request,root=ROOT)
        value={**value,'diagnostic':diagnostic,'error_code':diagnostic['error_code'],
               'incident_id':diagnostic['incident_id'],'lesson_ids':diagnostic['lesson_ids']}
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=path.name + '.',
                                suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
            stream.write('\n')
        for attempt in range(16):
            try:
                temporary.replace(path)
                return value
            except PermissionError as error:
                # A Windows reader can briefly deny replacement while polling STATUS.json.
                if getattr(error, 'winerror', None) not in (5, 32) or attempt == 15:
                    raise
                time.sleep(.02)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_failure(output,status):
    saved=save(output/'STATUS.json',status)
    fields=('state','message','error_code','incident_id','lesson_ids')
    print(json.dumps({'out':str(output),**{k:saved[k] for k in fields if k in saved}},ensure_ascii=False))
    return saved


def environment(data):
    from chinatravel.environment.sandbox.__main__ import configuration
    os.environ.update(configuration(Path(data)))
    from chinatravel.environment.world_env import WorldEnv
    return WorldEnv(lang='zh')


def fresh(path):
    path=Path(path).resolve()
    if path.exists(): raise ValueError('输出目录已存在，请使用新目录')
    path.mkdir(parents=True)
    return path


def normalize(value,env):
    from .request import normalize_request,VERSION
    return value if value.get('schema_version')==VERSION else normalize_request(value,env)


def facts(args):
    from .world_facts import WorldFacts

    environment(args.data)
    result = WorldFacts.configured(args.data).query(read(args.input))
    output = fresh(args.out)
    save(output / 'WORLD_FACTS.json', result)
    print(json.dumps({'out': str(output / 'WORLD_FACTS.json')}, ensure_ascii=False))


def sketch(args):
    from .trip_sketch import propose_trip_sketches

    snapshot = read(args.input)
    output = fresh(args.out)
    result = propose_trip_sketches(snapshot, use_model=args.with_model)
    path = output / 'TRIP_SKETCHES.json'
    save(path, result)
    print(json.dumps({'out': str(path), 'state': result['state'], 'count': len(result['sketches']),
                      'model_error': result['model']['error']}, ensure_ascii=False))


def sketch_trial(args):
    from .sketch_planning import optimize_day, plan_from_sketches
    from .world_facts import WorldFacts
    from .lessons import record_failure

    spec, snapshot = read(args.input), read(args.snapshot)
    env = environment(args.data)
    facts = WorldFacts.configured(args.data)
    folder = fresh(args.out)
    if args.command == 'trial':
        result = plan_from_sketches(spec, snapshot, read(args.layouts), env, facts, max_seconds=args.trial_seconds,
                                    max_candidates=args.candidates,
                                    on_first=lambda entry: save(folder / 'FIRST_CANDIDATE.json', entry))
        path = folder / 'CANDIDATE_ARCHIVE.json'
        if result['state'] != 'complete':
            code = 'search_exhausted' if result['state'] == 'no_verified_candidate' else result['state']
            record_failure(folder, {**result, 'reason_code': code}, spec)
    else:
        source = read(args.plan_file)
        plan = source['itinerary'] if isinstance(source.get('itinerary'), dict) else source
        result = optimize_day(spec, snapshot, plan, args.day, env, facts)
        path = folder / 'ROUTE_OPTIMIZATION.json'
    save(path, result)
    print(json.dumps({'out': str(path), 'state': result['state']}, ensure_ascii=False))


def assess(args):
    from .lessons import record_failure
    from .plan_assessment import evaluate_archive
    from .world_facts import WorldFacts

    spec, snapshot, archive = read(args.input), read(args.snapshot), read(args.archive)
    env = environment(args.data)
    folder = fresh(args.out)
    result = evaluate_archive(spec, snapshot, archive, env, WorldFacts.configured(args.data))
    path = folder / 'PLAN_ASSESSMENT.json'
    save(path, result)
    if result['state'] != 'complete':
        record_failure(folder, {'state': result['state'], 'reason_code': 'no_verified_result',
                                'stage': 'plan_assessment', 'message': '本次评估没有可推荐的核验候选。',
                                'evidence': {'candidate_count': result['candidate_count']}}, spec)
    print(json.dumps({'out': str(path), 'state': result['state'], 'verified': result['verified_count'],
                      'recommended_id': result['selection']['recommended_id']}, ensure_ascii=False))


def integrated_job(args):
    from .integrated_planning import run_trip_plan, run_trip_route
    from .lessons import describe_exception
    from .world_facts import WorldFacts

    output = fresh(args.out)
    stage = 'data_setup'
    try:
        env = environment(args.data)
        facts = WorldFacts.configured(args.data)
        stage = 'route' if args.command == 'trip-route' else 'input'
        if args.command == 'hotel-preview':
            from .hotel_change import hotel_preview
            stage='hotel_change'
            status=hotel_preview(args.input,output,args.candidate_id,env,facts)
        elif args.command == 'edit-preview':
            from .itinerary_edit import edit_preview
            stage = 'itinerary_edit'
            status = edit_preview(args.input, output, args.candidate_id, read(args.edit_file), env, facts)
        elif args.command == 'trip-route':
            status = run_trip_route(args.input, output, args.candidate_id, args.day, env, facts,
                                    view_only=args.view_only)
        else:
            runner = run_trip_plan
            recovery_options = {}
            if args.recover:
                from .trip_recovery import run_trip_with_recovery
                runner = run_trip_with_recovery
                recovery_options['try_budget_alternatives'] = args.try_budget_alternatives
            deadline = time.perf_counter() + args.search_limit if args.search_limit is not None else None
            status = runner(read(args.input), output, env, facts, model=args.with_model, search_deadline=deadline,
                            seconds=args.trial_seconds, candidates=args.candidates, quality_seconds=args.quality_seconds,
                            model_after_search=args.model_after_search,
                            **recovery_options)
    except Exception as error:
        status = save(output / 'STATUS.json', {**describe_exception(error, stage), 'protocol': 'integrated_planning_v1'})
    print(json.dumps({'out': str(output), 'state': status['state']}, ensure_ascii=False))


def prepare(args):
    env=environment(args.data)
    from .catalog import PreferenceCatalog
    from .city_scope import prepare_for_planning
    from .compiler import compile_request
    output=fresh(args.out)
    request=prepare_for_planning(normalize(read(args.input),env))
    save(output/'request.json',request)
    save(output/'options.json',PreferenceCatalog(env,request['trip']['destination_city']).options())
    if request['ready']: save(output/'query_bundle.json',compile_request(request))
    print(json.dumps({'ready':request['ready'],'days':request['trip']['days'],'preferences':len(request['preferences']),
                      'issues':request['issues'],'out':str(output)},ensure_ascii=False))


def convert(args):
    env=environment(args.data)
    from .request import normalize_request
    output=fresh(args.out)
    dataset=ROOT.parent/'datasets/ChinaTravel'
    datasets={}
    for name in ['easy','medium','human','preference_base50']+[f'preference{i}_base50' for i in range(6)]:
        with (dataset/(name+'.csv')).open(encoding='utf-8-sig',newline='') as stream: datasets[name]=list(csv.DictReader(stream))
    known={r['uid'] for k,rows in datasets.items() if k in ('easy','medium','human') for r in rows}
    regression=read(ROOT/'experiments/query_sets/ten_city_xian_v1.json')
    for tier,rows in regression['cases'].items(): datasets['regression_'+tier]=rows
    xian=Path(args.data)/'query_sets/xian11'
    for tier in ('easy','medium','human'):
        datasets['xian_'+tier]=[read(p) for p in sorted((xian/tier).glob('*.json'))]
    summary={}; references=[]; requests=[]; audits=[]
    with (output/'requests.jsonl').open('w',encoding='utf-8') as stream:
        for source,rows in datasets.items():
            count=ready=0; day_counts={}; unresolved=0
            for row in rows:
                public={k:v for k,v in row.items() if k not in {'hard_logic','hard_logic_py','hard_logic_nl','preference_py','preference','preference_en','soft_logic'}}
                for field in ('days','people_number'):
                    if public.get(field) is not None: public[field]=int(public[field])
                try: request=normalize_request(public,env)
                except (ValueError,TypeError,KeyError) as error:
                    audits.append({'source':source,'uid':row.get('uid'),'status':'conversion_error','error':type(error).__name__,'message':str(error)})
                    continue
                uid=row['uid']; base_uid=uid[2:] if uid[:2] in ('p0','p1','p2','p3','p4','p5') and uid[2:] in known else uid
                request['source']={'namespace':source,'uid':uid,'base_uid':base_uid,
                    'raw_text_sha256':hashlib.sha256(request['raw_text'].encode()).hexdigest()}
                stream.write(json.dumps(request,ensure_ascii=False)+'\n')
                references.append({'namespace':source,'uid':uid,'base_uid':base_uid,
                    'reference':{k:row[k] for k in ('hard_logic','hard_logic_py','preference_py') if k in row}})
                audits.append({'source':source,'uid':uid,'ready':request['ready'],'issues':request['issues'],
                               'unmapped_requirements':request['unmapped_requirements']})
                count+=1; ready+=int(request['ready']); unresolved+=bool(request['unmapped_requirements'])
                day_counts[str(request['trip']['days'])]=day_counts.get(str(request['trip']['days']),0)+1
            summary[source]={'input_rows':len(rows),'converted':count,'ready':ready,'with_unmapped':unresolved,'days':day_counts}
    for name,rows in [('references',references),('conversion_audit',audits)]:
        (output/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    save(output/'SUMMARY.json',summary)
    print(json.dumps({'summary':summary,'out':str(output),'model_calls':0},ensure_ascii=False))


def plan(args):
    started=time.perf_counter();output=fresh(args.out);stage={'name':'data_setup'}
    try:return _plan(args,output,stage,started)
    except Exception as error:
        from .lessons import describe_exception
        report=describe_exception(error,stage['name'])
        report['end_to_end_seconds']=time.perf_counter()-started
        publish_failure(output,report)
        return 2


def _plan(args,output,stage,started):
    env=environment(args.data)
    from .city_scope import frozen_city_outcome, prepare_for_planning
    from .compiler import compile_request
    from .catalog import PreferenceCatalog
    from .evaluation import evaluate_preferences
    from chinatravel.soft_constraints.pool import fingerprint
    stage['name']='normalization'
    request=prepare_for_planning(normalize(read(args.input),env))
    save(output/'request.json',request)
    frozen=frozen_city_outcome(request)
    if frozen:
        publish_failure(output,frozen)
        return 2
    if not request['ready']:
        publish_failure(output,{'state':'needs_confirmation','reason_code':'needs_confirmation',
             'message':'需求存在冲突或尚未核实的硬条件，请先确认相关事项。','issues':request['issues'],'unmapped':request['unmapped_requirements']})
        return
    from .outcomes import failure_outcome
    from .lessons import LessonBook
    stage['name']='lesson_loading';book=LessonBook()
    stage['name']='preflight'
    lessons=book.check(request,env)
    save(output/'lessons_applied.json',lessons)
    (output/'lesson_rules_snapshot.json').write_bytes(book.raw)
    transport_issue=lessons['blocking']
    if transport_issue:
        transport_issue['end_to_end_seconds']=time.perf_counter()-started
        save(output/'transport_precheck.json',transport_issue)
        publish_failure(output,transport_issue);return
    if args.interpret_model and request['raw_text']:
        if not os.environ.get('DEEPSEEK_API_KEY'):
            publish_failure(output,{'state':'missing_model_credentials','reason_code':'missing_model_credentials',
                 'message':'模型服务密钥未配置，暂时无法解析补充偏好。'})
            return 2
        stage['name']='model_interpretation'
        from chinatravel.agent.llms import OpenAICompatibleLLM
        from .model import interpret_preferences
        model=OpenAICompatibleLLM('deepseek-flash',name='deepseek-flash',base_url='https://api.deepseek.com',
            api_key=os.environ['DEEPSEEK_API_KEY'],max_tokens=1500,
            default_request_args={'temperature':0,'timeout':30,'extra_body':{'thinking':{'type':'disabled'}}})
        model._client_kwargs.update(book.model_client_options())
        request=interpret_preferences(request,PreferenceCatalog(env,request['trip']['destination_city']),model)
        save(output/'request.json',request)
    stage['name']='requirement_compilation'
    bundle=compile_request(request)
    bundle['legacy_query']['experiment_options']={'candidate_recall':getattr(args,'candidate_recall','baseline')}
    save(output/'query_bundle.json',bundle)
    query=output/'planner_input.json'; save(query,bundle['legacy_query'])
    if args.provider=='deepseek' and not os.environ.get('DEEPSEEK_API_KEY'):
        publish_failure(output,{'state':'missing_model_credentials','reason_code':'missing_model_credentials',
             'message':'模型服务密钥未配置，暂时无法生成行程。需求与编译结果已保存。','environment_variable':'DEEPSEEK_API_KEY'})
        return 2
    run=output/'planning'
    common=['--query',str(query),'--out',str(run),'--arm','personalized','--seconds',str(args.seconds),
            '--candidate-limit',str(args.candidates),'--time-policy',args.time_policy]
    if args.provider=='deepseek':
        command=[sys.executable,'-B','-X','utf8',str(ROOT/'experiments/timing_probe/api_speed.py'),'full','--data',str(Path(args.data).resolve()),'--defer-audit',*common]
    else:
        command=[sys.executable,'-B','-X','utf8',str(ROOT/'experiments/timing_probe/run_activity_granularity.py'),'worker','--endpoint',args.endpoint,*common]
    save(output/'STATUS.json',{'state':'planning','started':datetime.now().isoformat()})
    child_env={**os.environ,'CHINATRAVEL_TRANSLATION_MODE':'chinatravel','PYTHONUTF8':'1',
               'SHORTTRIP_LESSON_RULES':str((output/'lesson_rules_snapshot.json').resolve())}
    stage['name']='search'
    with (output/'worker.log').open('w',encoding='utf-8') as log:
        child=subprocess.run(command,cwd=ROOT,env=child_env,stdout=log,stderr=subprocess.STDOUT)
    report={'state':'worker_failed','worker_exit':child.returncode,'end_to_end_seconds':time.perf_counter()-started}
    run_info=read(run/'run.json') if (run/'run.json').exists() else {}
    report.update(failure_outcome(run_info,child.returncode))
    if (run/'pool.json').exists():
        stage['name']='independent_verification'
        pool=read(run/'pool.json'); selected_plan=read(run/'plan.json')
        selected=fingerprint(selected_plan) if selected_plan.get('itinerary') else None
        catalog=PreferenceCatalog(env,request['trip']['destination_city'])
        from experiments.evaluation.validation import validate_plan
        audit={}
        for item in pool:
            hard=validate_plan(item['plan'],bundle['legacy_query'],config={'time_policy':args.time_policy})
            personal=evaluate_preferences(item['plan'],request,catalog)
            passed=all(c['status'] in ('pass','not_applicable') for c in hard['checks']) and personal['eligible']
            audit[item['fingerprint']]={'passes':passed,'hard_validation':hard,'preferences':personal}
        save(output/'independent_audit.json',audit)
        result=audit.get(selected,{})
        times=read(run/'candidate_times.json') if (run/'candidate_times.json').exists() else []
        first=next((r['seconds'] for r in times if audit.get(r['fingerprint'],{}).get('passes')),None)
        report.update(selected_fingerprint=selected,
                      first_independent_legal_seconds=first,
                      internal_candidates=len(pool),independent_legal_candidates=sum(r['passes'] for r in audit.values()),
                      selected_legal=bool(result.get('passes')),preferences=result.get('preferences'))
        if selected and not result.get('passes'):
            report['selected_failed_checks']=[c for c in result.get('hard_validation',{}).get('checks',[]) if c['status'] not in ('pass','not_applicable')]
        if result.get('passes'):
            report.update(state='complete',message='已找到通过核验的行程。',reason_code='verified_plan')
            save(output/'plan.json',selected_plan)
        else:report.update(failure_outcome(run_info,child.returncode,len(pool)))
    report['pre_audit_wall_seconds']=report['end_to_end_seconds']
    report['end_to_end_seconds']=time.perf_counter()-started
    if report['state']=='complete':
        save(output/'STATUS.json',report)
        print(json.dumps({'out':str(output),'state':report['state'],'selected_legal':report.get('selected_legal')},ensure_ascii=False))
    else:publish_failure(output,report)


def replay(args):
    env=environment(args.data)
    from .catalog import PreferenceCatalog
    from .evaluation import evaluate_preferences
    from .request import normalize_request
    output=fresh(args.out); pool=read(args.pool)
    raw=read(args.input); city=raw.get('target_city',raw.get('trip',{}).get('destination_city'))
    catalog=PreferenceCatalog(env,city); results={}
    profiles={'unchanged':{},'museum':{'interests':['museum']},'nature':{'interests':['nature']},
              'less_walking':{'mobility':'less_walking'},'lodging_cost':{'focus':'lodging_cost'}}
    for name,profile in profiles.items():
        request=normalize_request({**raw,'profile':profile},env)
        candidates=[]
        for item in pool:
            p=evaluate_preferences(item['plan'],request,catalog)
            old=item['score']['total_penalty']
            key=old+20*(1-(p['utility'] or 0)) if request['preferences'] else old
            candidates.append({'fingerprint':item['fingerprint'],'selection_penalty':key,'base_penalty':old,'report':p})
        eligible=[c for c in candidates if c['report']['eligible']]
        results[name]={'selected':min(eligible,key=lambda c:c['selection_penalty']) if eligible else None,'candidates':candidates}
    save(output/'RESULTS.json',results)
    print(json.dumps({'out':str(output),'pool_size':len(pool),'selected':{k:v['selected']['fingerprint'] if v['selected'] else None for k,v in results.items()},'model_calls':0},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','convert','plan','replay','facts','sketch','trial','optimize-day',
                                           'assess','trip-run','trip-route','hotel-preview','edit-preview','lessons-report'])
    parser.add_argument('--input',type=Path)
    parser.add_argument('--pool',type=Path)
    parser.add_argument('--out',type=Path,default=ROOT/'result/manual'/datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    parser.add_argument('--data',type=Path,default=DATA)
    parser.add_argument('--provider',choices=['deepseek','qwen'],default='deepseek')
    parser.add_argument('--seconds',type=float,default=1800)
    parser.add_argument('--candidates',type=int,default=10)
    parser.add_argument('--time-policy',choices=['strict','flexible'],default='flexible')
    parser.add_argument('--endpoint',default='http://127.0.0.1:18080/v1')
    parser.add_argument('--interpret-model',action='store_true',help='Use a grounded model call for additional soft wishes before planning')
    parser.add_argument('--candidate-recall',choices=['baseline','dedupe_only','trip_api'],default='baseline',
                        help='Opt-in once-per-trip model recall over the complete city catalog')
    parser.add_argument('--with-model',action='store_true',help='Use one DeepSeek request for the sketch command')
    parser.add_argument('--model-after-search', action='store_true',
                        help='Use remaining time to try a model alternative after conventional search')
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--layouts',type=Path)
    parser.add_argument('--plan-file',type=Path)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--candidate-id')
    parser.add_argument('--edit-file', type=Path, help='Operation for a verified itinerary edit')
    parser.add_argument('--day',type=int)
    parser.add_argument('--view-only',action='store_true',help='Show transport for the existing order in trip-route')
    parser.add_argument('--trial-seconds',type=float,default=24)
    parser.add_argument('--search-limit', type=float, help='Shared time limit for planning, extra search and recovery (up to 60 s)')
    parser.add_argument('--quality-seconds', type=float, default=30,
                        help='Seconds to test nearby experiences after initial search (0 for baseline)')
    parser.add_argument('--recover', action='store_true', help='Try verified alternatives after trip-run fails')
    parser.add_argument('--try-budget-alternatives', action='store_true', help='Allow recovery to preview a higher budget')
    args=parser.parse_args()
    if args.search_limit is not None and not 0 < args.search_limit <= 60:
        parser.error('--search-limit must be positive and at most 60 seconds')
    if not 0 <= args.quality_seconds <= 90:
        parser.error('--quality-seconds must be between 0 and 90')
    if args.command not in ('convert','lessons-report') and not args.input: parser.error('--input is required')
    if args.command=='replay' and not args.pool: parser.error('--pool is required')
    if args.command=='trial' and (not args.snapshot or not args.layouts):
        parser.error('trial requires --snapshot and --layouts')
    if args.command=='optimize-day' and (not args.snapshot or not args.plan_file or args.day is None):
        parser.error('optimize-day requires --snapshot, --plan-file and --day')
    if args.command=='assess' and (not args.snapshot or not args.archive):
        parser.error('assess requires --snapshot and --archive')
    if args.command=='trip-route' and (not args.candidate_id or args.day is None):
        parser.error('trip-route requires --candidate-id and --day')
    if args.command=='edit-preview' and (not args.candidate_id or not args.edit_file):
        parser.error('edit-preview requires --candidate-id and --edit-file')
    if args.seconds<=0 or args.candidates<1: parser.error('规划时限与候选目标必须为正')
    if args.command=='lessons-report':
        from .lessons import write_lesson_review
        print(json.dumps({'review_file':str(write_lesson_review())},ensure_ascii=False));return 0
    handlers = {'prepare': prepare, 'convert': convert, 'plan': plan, 'replay': replay, 'facts': facts,
                'sketch': sketch, 'trial': sketch_trial, 'optimize-day': sketch_trial, 'assess': assess,
                'trip-run': integrated_job, 'trip-route': integrated_job, 'hotel-preview': integrated_job,
                'edit-preview': integrated_job}
    return handlers[args.command](args)
