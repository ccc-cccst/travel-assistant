"""Local preference form connected to normalization and the fused planner."""
import argparse
from datetime import datetime, timedelta, timezone
from functools import lru_cache, partial
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs,urlparse

from .cli import ROOT,DATA,environment,read,save
from .catalog import PreferenceCatalog
from .city_scope import frozen_city_outcome, planning_cities, prepare_for_planning
from .lessons import record_failure,describe_exception


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8780)
    parser.add_argument('--data',type=Path,default=DATA)
    args=parser.parse_args(); env=environment(args.data)
    from .request import normalize_request
    from .world_facts import WorldFacts
    from .local_model import local_model_available, request_local_json
    from .tripspec import extract_api, interpret, normalize_saved_spec, resolve_conflicts
    facts = WorldFacts.configured(args.data)
    cities=planning_cities(env.support_cities)
    jobs={}; catalog={city:PreferenceCatalog(env,city).options() for city in cities}
    trip_specs=ROOT/'result/tripspec'
    revision_lock=Lock()
    def model_options():
        return {'deepseek': bool(os.environ.get('DEEPSEEK_API_KEY', '').strip()),
                'qwen': local_model_available(), 'program': True}

    def unavailable_model(selected):
        if selected in (None, 'program'):
            return None
        if model_options().get(selected):
            return None
        messages = {'deepseek': 'DeepSeek 尚未就绪，请检查后台配置或切换规划方式。',
                    'qwen': '本地 Qwen 尚未就绪，请启动本地模型或切换规划方式。'}
        return {'message': messages[selected], 'reason_code': 'model_provider_unavailable'}

    @lru_cache(maxsize=16)
    def archived_cards(source):
        from .integrated_planning import _card
        original = read(source/'PLAN_VIEW.json')
        raw = {c['id']: c for c in read(source/'CANDIDATE_ARCHIVE.json')['archive']}
        assessed = {c['id']: c for c in read(source/'PLAN_ASSESSMENT.json')['candidates']}
        spec = read(source/'request.json')
        # The saved plan determines order and prices. Only use catalog details from the same installation.
        matching = read(source/'WORLD_FACTS.json')['data'] == facts.version
        return {**original, 'candidates': [_card(raw[c['id']], assessed[c['id']], spec,
                                                 facts if matching else None) for c in original['candidates']]}

    def itinerary_view(source, view):
        if all(card.get('guide_version') == 1 for card in view['candidates']):
            return view
        if not all((source/name).is_file() for name in
                   ('request.json','WORLD_FACTS.json','PLAN_VIEW.json','CANDIDATE_ARCHIVE.json','PLAN_ASSESSMENT.json')):
            return view
        try:
            return archived_cards(source)
        except (KeyError, ValueError, TypeError):
            return view

    def valid_id(identifier):
        return (isinstance(identifier,str) and len(identifier)==32
                and all(c in 'abcdef0123456789' for c in identifier))

    def latest_spec(identifier):
        if not valid_id(identifier):
            raise ValueError('需求编号无效。')
        path=trip_specs/identifier/'latest.json'
        return normalize_saved_spec(read(path)) if path.is_file() else None

    def recovery_source(folder, job_id, recovery):
        from .trip_recovery import ATTEMPT_IDS
        if recovery and recovery.get('restored') in ATTEMPT_IDS:
            return folder / recovery['restored']
        original = read(folder/'request.json') if (folder/'request.json').is_file() else {}
        if valid_id(original.get('request_id')):
            current = latest_spec(original['request_id'])
            adoption = (current or {}).get('recovery_adoption', {})
            if adoption.get('job_id') == job_id and adoption.get('attempt_id') in ATTEMPT_IDS:
                return folder / adoption['attempt_id']
        return folder

    def checked_recovery_preview(folder, recovery, attempt_id):
        """Use only a published complete preview from this job and data version."""
        attempt = next((a for a in recovery['attempts'] if a['id'] == attempt_id and a.get('view')), None)
        if attempt is None:
            return None
        source = folder/attempt_id
        if not all((source/name).is_file() for name in ('WORLD_FACTS.json', 'STATUS.json', 'PLAN_VIEW.json')):
            return None
        snapshot = read(source/'WORLD_FACTS.json')
        if (snapshot['data'] != facts.version or snapshot['snapshot_id'] != attempt['snapshot_id']
                or read(source/'STATUS.json')['state'] != 'complete'):
            return None
        return source

    def start_job(command, source, options=()):
        job_id = uuid.uuid4().hex
        folder = ROOT / 'result/manual' / job_id
        if isinstance(source, dict):
            source_file = ROOT / 'result/inputs' / (job_id + '.json')
            save(source_file, source)
        else:
            source_file = source
        logs = ROOT / 'result/server_logs'
        logs.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, '-B', '-X', 'utf8', str(ROOT / 'personalize.py'), command,
                '--input', str(source_file), '--out', str(folder), '--data', str(args.data.resolve()), *options]
        child_env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
        with (logs / (job_id + '.log')).open('w', encoding='utf-8') as log:
            process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=child_env,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        jobs[job_id] = (folder, process)
        return {'id': job_id, 'state': 'starting'}

    class Handler(BaseHTTPRequestHandler):
        def fail(self,http_status,status,request=None):
            directory=ROOT/'result/api_errors'/uuid.uuid4().hex
            diagnostic=record_failure(directory,status,request)
            self.reply(http_status,{**status,'error':status['message'],'diagnostic':diagnostic,
                                    'error_code':diagnostic['error_code'],'incident_id':diagnostic['incident_id']})

        def reply(self,status,value):
            body=json.dumps(value,ensure_ascii=False,default=str).encode()
            self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            url=urlparse(self.path); query=parse_qs(url.query)
            if url.path in ('/','/tripspec'):
                page='tripspec.html' if url.path=='/tripspec' else 'index.html'
                body=(ROOT/'web'/page).read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body)
            elif url.path=='/options':
                city=query.get('city',['上海'])[0]
                providers = model_options()
                self.reply(200,{'cities':cities,'options':catalog.get(city),
                                'model_ready':providers['deepseek'], 'model_providers':providers})
            elif url.path=='/tripspec/places':
                city = query.get('city', [''])[0]
                if city not in cities:
                    return self.reply(400, {'message':'请先确认可规划的目的城市。'})
                try:
                    choices = facts.attraction_choices(city, query.get('q', [''])[0])
                except ValueError as error:
                    return self.reply(400, {'message':str(error)})
                return self.reply(200, {'places':choices})
            elif url.path=='/tripspec/state':
                try:
                    current=latest_spec(query.get('id',[''])[0])
                except ValueError as error:
                    return self.reply(400,{'message':str(error)})
                self.reply(200 if current else 404,current or {'message':'未找到已保存的需求。'})
            elif url.path=='/job':
                job=jobs.get(query.get('id',[''])[0])
                if not job:return self.fail(404,{'state':'task_not_found','reason_code':'task_not_found',
                    'message':'未找到该规划任务，任务可能不存在或服务已重启。请重新规划。'})
                folder,process=job
                status=read(folder/'STATUS.json') if (folder/'STATUS.json').exists() else {'state':'starting'}
                ledger=folder/'planning/requests.jsonl'
                if ledger.exists():
                    lines=ledger.read_text(encoding='utf-8').splitlines()
                    status['completed_model_calls']=len(lines)
                if process.poll() is not None and status.get('state') in ('starting','planning'):
                    status={'state':'worker_failed','reason_code':'worker_failed','stage':'worker',
                            'message':'规划进程意外结束，尚未生成完成记录。请重试或查看错误编号。',
                            'evidence':{'worker_exit':process.returncode}}
                    save(folder/'STATUS.json',status)
                    status=read(folder/'STATUS.json')
                if (folder/'plan.json').exists(): status['plan']=read(folder/'plan.json')
                self.reply(200,status)
            elif url.path=='/trip-planning/job':
                job_id=query.get('id',[''])[0]
                folder=ROOT/'result/manual'/job_id if valid_id(job_id) else None
                job=jobs.get(job_id)
                path=folder/'STATUS.json' if folder else None
                status=read(path) if path and path.is_file() else None
                if not job and (not status or status.get('protocol')!='integrated_planning_v1'):
                    return self.fail(404,{'state':'task_not_found','reason_code':'task_not_found',
                                          'message':'未找到这次规划任务。请重新规划。'})
                status=status or {'state':'starting'}
                if job and job[1].poll() is not None and status['state'] in ('starting','planning'):
                    status=read(path) if path.is_file() else status
                    if status['state'] in ('starting','planning'):
                        status=save(path,{'state':'worker_failed','reason_code':'worker_failed','stage':'worker',
                                          'protocol':'integrated_planning_v1','message':'规划进程意外结束，请重试。',
                                          'evidence':{'worker_exit':job[1].returncode}})
                recovery_path = folder/'RECOVERY.json'
                recovery = read(recovery_path) if recovery_path.is_file() else None
                source = recovery_source(folder, job_id, recovery) if recovery else folder
                if source != folder:
                    original_failure = status['message']
                    status = read(source/'STATUS.json')
                    status['recovered'] = {'original_failure': original_failure,
                                           'change': next(a['change'] for a in recovery['attempts']
                                                          if a['id'] == source.name)}
                elif recovery:
                    if recovery['state'] in ('starting','running') and status['state'] not in ('starting','planning','complete'):
                        if job and job[1].poll() is None:
                            status.update(state='planning', stage='recovery')
                        else:
                            recovery = {**recovery, 'state': 'interrupted'}
                    from .trip_recovery import ATTEMPT_IDS
                    for attempt in recovery.get('attempts', ()):
                        if attempt.get('view') and attempt.get('id') in ATTEMPT_IDS:
                            attempt['view'] = itinerary_view(folder/attempt['id'], attempt['view'])
                    status['recovery'] = recovery
                for name,file in (('first','FIRST_VIEW.json'),('result','PLAN_VIEW.json'),('route','ROUTE_VIEW.json')):
                    view=source/file
                    if view.is_file():
                        status[name]=read(view)
                        if name=='result':
                            status[name]=itinerary_view(source,status[name])
                parent_file=ROOT/'result/inputs'/(job_id+'.hotel.json')
                if parent_file.is_file() and status.get('hotel_change'):
                    status['hotel_parent']=read(parent_file)
                    status['hotel_accepted']=(folder/'ADOPTED.json').is_file()
                self.reply(200,status)
            else: self.reply(404,{'error':'未找到入口'})

        def do_POST(self):
            request=None;stage='input'
            try:
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=65536:return self.fail(400,{'state':'needs_confirmation','reason_code':'invalid_input','message':'需求内容长度无效，请重新填写。'})
                value=json.loads(self.rfile.read(size))
                if self.path=='/itinerary-edit/intent':
                    from .edit_intent import interpret_edit
                    if not isinstance(value, dict):
                        return self.reply(400, {'message':'请输入修改请求。'})
                    selected = value.get('model_provider')
                    if selected == 'program':
                        return self.reply(200, {'state':'unavailable', 'message':'本行程使用表单规划，可以在时间线或地图手动修改。'})
                    if selected not in (None, 'deepseek', 'qwen'):
                        return self.reply(400, {'message':'不支持该行程的模型选项。'})
                    if unavailable := unavailable_model(selected):
                        return self.reply(503, unavailable)
                    try:
                        provider = {'provider': request_local_json} if selected == 'qwen' else {}
                        result = interpret_edit(value, facts, **provider)
                    except (ValueError, KeyError, TypeError):
                        return self.reply(400, {'message':'无法读取当前行程或修改请求。请重新打开方案后再试。'})
                    return self.reply(200, result)
                if self.path=='/tripspec/interpret':
                    if not isinstance(value, dict):
                        raise ValueError('输入必须为对象。')
                    selected = value.get('model_provider')
                    if selected not in (None, 'deepseek', 'qwen', 'program'):
                        raise ValueError('请选择支持的规划方式。')
                    if unavailable := unavailable_model(selected):
                        return self.reply(503, unavailable)
                    raw_text = value.get('raw_text', value.get('nature_language', ''))
                    if selected == 'program' and isinstance(raw_text, str) and raw_text.strip():
                        return self.reply(400, {'message':'仅用表单时，请使用表单字段填写需求。'})
                    stage='tripspec_extraction'
                    extractor = partial(extract_api, provider=request_local_json) if selected == 'qwen' else extract_api
                    request=interpret(value,env.support_cities,provider=extractor,
                                      reference_date=datetime.now(timezone(timedelta(hours=8))).date())
                    if selected is not None:
                        request['model_provider'] = selected
                    request['request_id']=uuid.uuid4().hex
                    folder=trip_specs/request['request_id']
                    save(folder/'revision_1.json',request)
                    save(folder/'latest.json',request)
                    return self.reply(200,request)
                if self.path=='/tripspec/resolve':
                    stage='tripspec_confirmation'
                    if not isinstance(value,dict):
                        raise ValueError('输入必须为对象。')
                    with revision_lock:
                        current=latest_spec(value.get('request_id'))
                        if not current:
                            return self.reply(404,{'message':'未找到已保存的需求。'})
                        if value.get('request_revision')!=current['request_revision']:
                            return self.reply(409,{'message':'需求已有新版本，请刷新后继续。'})
                        request=resolve_conflicts(current,value.get('choices'))
                        folder=trip_specs/current['request_id']
                        save(folder/f"revision_{request['request_revision']}.json",request)
                        save(folder/'latest.json',request)
                    return self.reply(200,request)
                if self.path=='/tripspec/place-preference':
                    from .place_preferences import update_place_preference
                    if not isinstance(value, dict):
                        return self.reply(400, {'message':'请选择景点。'})
                    with revision_lock:
                        current = latest_spec(value.get('request_id'))
                        if not current:
                            return self.reply(404, {'message':'未找到已保存的需求。'})
                        if value.get('request_revision') != current['request_revision']:
                            return self.reply(409, {'message':'需求已有新版本，请刷新后继续。'})
                        try:
                            request = update_place_preference(current, facts, value.get('place_id'), value.get('choice'))
                        except ValueError as error:
                            return self.reply(400, {'message':str(error)})
                        if request is not current:
                            folder = trip_specs/current['request_id']
                            save(folder/f"revision_{request['request_revision']}.json",request)
                            save(folder/'latest.json',request)
                    return self.reply(200, request)
                if self.path=='/facts':
                    stage='world_facts'
                    try:
                        result = facts.query(value)
                    except ValueError as error:
                        return self.fail(400, {'state':'needs_confirmation','reason_code':'invalid_input',
                                               'stage':stage,'message':str(error)})
                    return self.reply(200,result)
                if self.path=='/trip-planning':
                    if (not isinstance(value, dict) or not valid_id(value.get('request_id'))
                            or type(value.get('request_revision')) is not int):
                        raise ValueError('请提供有效的需求编号和版本。')
                    with revision_lock:
                        request=latest_spec(value['request_id'])
                        if not request:
                            return self.reply(404,{'message':'未找到已保存的需求。'})
                        if value.get('request_revision')!=request['request_revision']:
                            return self.reply(409,{'message':'需求已有新版本，请重新核对。'})
                    budget_option = value.get('try_budget_alternatives', False)
                    if type(budget_option) is not bool:
                        raise ValueError('请选择是否允许试排更高预算。')
                    selected = request.get('model_provider')
                    if unavailable := unavailable_model(selected):
                        return self.reply(503, unavailable)
                    options=['--candidates','15','--search-limit','60','--recover']
                    if selected != 'program':
                        options.append('--with-model')
                    if selected != 'program' and os.environ.get('SHORTTRIP_MODEL_AFTER_SEARCH') == '1':
                        options.append('--model-after-search')
                    if budget_option:
                        options.append('--try-budget-alternatives')
                    stage='task_start'
                    return self.reply(202,start_job('trip-run', request, options))
                if self.path=='/trip-planning/recovery/accept':
                    from .trip_recovery import ATTEMPT_IDS
                    if (not isinstance(value, dict) or not valid_id(value.get('job_id'))
                            or value.get('attempt_id') not in ATTEMPT_IDS):
                        raise ValueError('请选择一个已核验的调整方案。')
                    job_id, attempt_id = value['job_id'], value['attempt_id']
                    folder = ROOT/'result/manual'/job_id
                    if not (folder/'RECOVERY.json').is_file():
                        return self.reply(404, {'message':'未找到这次恢复任务。'})
                    recovery = read(folder/'RECOVERY.json')
                    child = checked_recovery_preview(folder, recovery, attempt_id)
                    if child is None:
                        return self.reply(409, {'message':'这项调整尚无可采用的完整行程，或数据已变化。请重新规划。'})
                    original = read(folder/'request.json')
                    request = read(child/'request.json')
                    adoption = {'job_id': job_id, 'attempt_id': attempt_id}
                    with revision_lock:
                        current = latest_spec(original['request_id'])
                        if current and current.get('recovery_adoption') == adoption:
                            return self.reply(200, current)
                        if current != original:
                            return self.reply(409, {'message':'需求已有新版本，请按最新需求规划。'})
                        for item in request['items']:
                            for evidence in item.get('additional_sources', []):
                                if evidence['type'] == 'recovery_assumption':
                                    evidence['type'] = 'user_confirmation'
                        request['recovery_adoption'] = adoption
                        revisions = trip_specs/request['request_id']
                        save(revisions/f"revision_{request['request_revision']}.json",request)
                        save(revisions/'latest.json',request)
                    return self.reply(200,request)
                if self.path=='/trip-planning/hotel/accept':
                    job_id=value.get('job_id')
                    if not valid_id(job_id):
                        raise ValueError('请选择已核验的换酒店预览。')
                    folder=ROOT/'result/manual'/job_id
                    with revision_lock:
                        parent=ROOT/'result/inputs'/(job_id+'.hotel.json')
                        status=read(folder/'STATUS.json') if (folder/'STATUS.json').is_file() else {}
                        if not parent.is_file() or not status.get('hotel_change') or status.get('state')!='complete':
                            return self.reply(409,{'message':'换酒店的预览尚未通过核验。'})
                        current=latest_spec(status['request_id'])
                        if not current or current['request_revision']!=status['request_revision']:
                            return self.reply(409,{'message':'需求已有新版本，请重新规划。'})
                        save(folder/'ADOPTED.json',read(parent))
                    return self.reply(200,{'id':job_id,'accepted':True})
                if self.path in ('/trip-planning/route','/trip-planning/hotel'):
                    if not isinstance(value,dict) or not valid_id(value.get('job_id')):
                        raise ValueError('请提供有效的规划任务编号。')
                    source=ROOT/'result/manual'/value['job_id']
                    recovery_path=source/'RECOVERY.json'
                    attempt_id=value.get('attempt_id')
                    if self.path=='/trip-planning/route' and attempt_id is not None:
                        from .trip_recovery import ATTEMPT_IDS
                        if attempt_id not in ATTEMPT_IDS:
                            raise ValueError('请选择已核验的调整方案。')
                        if not recovery_path.is_file() or not (source/'request.json').is_file():
                            return self.reply(409, {'message':'这次任务还没有可查看的调整方案。'})
                        original = normalize_saved_spec(read(source/'request.json'))
                        with revision_lock:
                            current = latest_spec(original['request_id'])
                            adopted = {'job_id': value['job_id'], 'attempt_id': attempt_id}
                            if not current or (current != original and current.get('recovery_adoption') != adopted):
                                return self.reply(409, {'message':'需求已有新版本，请按最新版本查看路线。'})
                        source=checked_recovery_preview(source,read(recovery_path),attempt_id)
                        if source is None:
                            return self.reply(409, {'message':'这项调整尚未生成可查看的完整行程。'})
                    elif recovery_path.is_file():
                        source=recovery_source(source,value['job_id'],read(recovery_path))
                    plan_file=source/'PLAN_VIEW.json'
                    if not plan_file.is_file():
                        return self.reply(409,{'message':'请等待方案比较完成。'})
                    plan_view=read(plan_file)
                    candidate=next((c for c in plan_view['candidates'] if c['id']==value.get('candidate_id')),None)
                    if self.path=='/trip-planning/hotel':
                        if not candidate:
                            raise ValueError('请选择已核验的原方案。')
                        stage='task_start'
                        job=start_job('hotel-preview',source,['--candidate-id',candidate['id']])
                        save(ROOT/'result/inputs'/(job['id']+'.hotel.json'),
                             {'job_id':value['job_id'],'candidate_id':candidate['id']})
                        return self.reply(202,job)
                    day=value.get('day')
                    if not candidate or type(day) is not int or not 1<=day<=len(candidate['days']):
                        raise ValueError('请选择已核验的候选与有效日序。')
                    view_only=value.get('view_only',False)
                    if type(view_only) is not bool:
                        raise ValueError('请选择查看走法或顺路优化。')
                    stage='task_start'
                    options=['--candidate-id',candidate['id'],'--day',str(day)]
                    if view_only:
                        options.append('--view-only')
                    return self.reply(202,start_job('trip-route', source, options))
                stage='normalization'
                request=prepare_for_planning(normalize_request(value,env))
                frozen=frozen_city_outcome(request)
                if self.path=='/prepare':
                    if not request['ready']:
                        diagnostic=record_failure(ROOT/'result/api_errors'/uuid.uuid4().hex,
                            frozen or {'state':'needs_confirmation','reason_code':'needs_confirmation','message':'需求存在冲突或待确认事项。',
                             'issues':request['issues'],'unmapped':request['unmapped_requirements']},request)
                        request={**request,'diagnostic':diagnostic,'error_code':diagnostic['error_code']}
                    return self.reply(200,request)
                if self.path!='/plan': return self.reply(404,{'error':'未找到入口'})
                if frozen: return self.fail(409,frozen,request)
                if not request['ready']:return self.fail(409,{'state':'needs_confirmation','reason_code':'needs_confirmation',
                    'message':'请先处理待确认项。','issues':request['issues'],'unmapped':request['unmapped_requirements']},request)
                if not os.environ.get('DEEPSEEK_API_KEY'):return self.fail(409,{'state':'missing_model_credentials',
                    'reason_code':'missing_model_credentials','message':'模型服务尚未配置。需求已可确认，请配置后端模型服务后重试。'},request)
                stage='task_start'
                self.reply(202,start_job('plan', value))
            except Exception as error:self.fail(400 if stage in ('input','normalization') else 500,describe_exception(error,stage),request)

    print(f'http://127.0.0.1:{args.port}',flush=True)
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError as error:
        diagnostic=record_failure(ROOT/'result/api_errors'/uuid.uuid4().hex,describe_exception(error,'http_bind'))
        print(json.dumps(diagnostic,ensure_ascii=False),file=sys.stderr);return 2
    server.serve_forever()


if __name__=='__main__':
    try:raise SystemExit(main() or 0)
    except Exception as error:
        diagnostic=record_failure(ROOT/'result/api_errors'/uuid.uuid4().hex,describe_exception(error,'web_startup'))
        print(json.dumps(diagnostic,ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
