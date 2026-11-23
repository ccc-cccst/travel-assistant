"""Reviewed runtime lessons and immutable incident records, with observed evidence."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

ROOT=Path(__file__).resolve().parents[2]
RULES=ROOT/'lessons/rules.json'
HANDLERS={'transport_availability','appointment_intervals','identity_response_encoding'}
INFO={
    'no_eligible_intercity_transport':('TRIP-TRANSPORT-001','transport_precheck','data_capability',['L001','L002','L003'],'confirmed'),
    'same_day_ticket_window':('TRIP-TRANSPORT-002','transport_precheck','snapshot_time_conflict',['L041'],'confirmed'),
    'overlapping_appointments':('TRIP-INPUT-002','requirement_confirmation','constraint_conflict',['L006'],'confirmed'),
    'local_mode_conflict':('TRIP-INPUT-005','requirement_confirmation','constraint_conflict',['L029'],'confirmed'),
    'needs_confirmation':('TRIP-INPUT-001','requirement_confirmation','input',['L006'],'undetermined'),
    'planning_constraint_unsupported':('TRIP-INPUT-006','planning_scope','product_scope',[],'confirmed'),
    'lodging_evidence_unavailable':('TRIP-LODGING-001','lodging_precheck','data_capability',[],'confirmed'),
    'visit_evidence_unavailable':('TRIP-PLACE-001','world_facts','data_capability',['L049'],'confirmed'),
    'no_hotel_change':('TRIP-LODGING-002','hotel_change','search_result',['L050'],'confirmed'),
    'search_timeout':('TRIP-SEARCH-001','search','search_limit',['L001','L007'],'undetermined'),
    'search_exhausted':('TRIP-SEARCH-002','search','search_result',['L001','L007'],'undetermined'),
    'verification_failed':('TRIP-VERIFY-001','independent_verification','validation',['L006','L008'],'undetermined'),
    'missing_model_credentials':('TRIP-MODEL-001','model_setup','configuration',[],'confirmed'),
    'data_snapshot_unavailable':('TRIP-DATA-001','data_setup','configuration',['L004'],'confirmed'),
    'planning_city_frozen':('TRIP-CITY-001','planning_scope','product_scope',[],'confirmed'),
    'lesson_rules_invalid':('TRIP-LESSON-001','lesson_loading','configuration',[],'confirmed'),
    'worker_failed':('TRIP-RUNTIME-001','worker','runtime',[],'undetermined'),
    'model_request_failed':('TRIP-MODEL-002','model_request','model',[],'undetermined'),
    'model_access_denied':('TRIP-MODEL-003','model_request','model_configuration',[],'confirmed'),
    'model_rate_limited':('TRIP-MODEL-004','model_request','model_limit',[],'confirmed'),
    'invalid_input':('TRIP-INPUT-003','input','input',[],'confirmed'),
    'input_file_unavailable':('TRIP-INPUT-004','input','input',[],'confirmed'),
    'task_not_found':('TRIP-JOB-001','job_lookup','request',[],'undetermined'),
    'server_start_failed':('TRIP-SERVER-001','http_bind','configuration',[],'confirmed'),
    'no_verified_result':('TRIP-UNKNOWN-001','result','undetermined',[],'undetermined'),
}


def scrub(value):
    if isinstance(value,dict):
        return {str(k):('[redacted]' if re.search(r'api.?key|authorization|password|secret|token$',str(k),re.I) else scrub(v)) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [scrub(v) for v in value]
    if isinstance(value,str):return re.sub(r'sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+','[redacted]',value,flags=re.I)
    return value


def describe_exception(error,stage):
    reason='worker_failed';message='运行时出现异常，未能完成规划。请凭错误编号查看任务记录。'
    if stage=='data_setup':reason,message='data_snapshot_unavailable','旅行数据快照未能加载，暂时无法规划。请检查数据安装与路径。'
    elif stage=='lesson_loading':reason,message='lesson_rules_invalid','经验规则文件无效，未能完成运行前检查。请检查经验规则配置。'
    elif stage=='http_bind':reason,message='server_start_failed','本地页面服务端口无法监听，请检查端口占用与运行权限。'
    elif stage in ('input','normalization') and isinstance(error,FileNotFoundError):
        reason,message='input_file_unavailable','需求文件无法读取，请检查输入文件路径。'
    elif stage in ('input','normalization','requirement_compilation') and isinstance(error,(ValueError,TypeError,KeyError,json.JSONDecodeError)):
        reason,message='invalid_input','输入未能解析或编译，请检查需求格式和待确认项。'
    elif stage=='model_interpretation':reason,message='model_request_failed','模型未能完成补充需求解析，请重试或改用明确的偏好选项。'
    return {'state':'worker_failed' if reason not in ('invalid_input',) else 'needs_confirmation',
            'reason_code':reason,'message':message,'stage':stage,
            'evidence':{'exception_type':type(error).__name__,'exception_detail':scrub(str(error))[:1000]}}


class LessonBook:
    def __init__(self,path=None):
        self.path=Path(path or os.environ.get('SHORTTRIP_LESSON_RULES') or RULES)
        raw=self.path.read_bytes();self.raw=raw;self.sha256=hashlib.sha256(raw).hexdigest()
        value=json.loads(raw.decode('utf-8-sig'))
        if value.get('schema_version')!='lesson_rules_v1' or not isinstance(value.get('rules'),list):raise ValueError('Invalid lesson schema')
        seen=set()
        for rule in value['rules']:
            if rule.get('id') in seen or not isinstance(rule.get('id'),str):raise ValueError('Invalid lesson ID')
            seen.add(rule['id'])
            if rule.get('handler') not in HANDLERS:raise ValueError('Unregistered lesson handler')
            if rule.get('phase') not in ('before_model','model_setup'):raise ValueError('Invalid lesson phase')
            if type(rule.get('enabled')) is not bool:raise ValueError('Invalid lesson enabled flag')
            if rule.get('enabled') and rule.get('review_status')=='verified':
                if not isinstance(rule.get('evidence'),list) or not rule['evidence'] or any(not isinstance(e,str) or not e for e in rule['evidence']):raise ValueError('Verified lesson requires evidence')
                if not isinstance(rule.get('regression_test'),str) or not rule['regression_test']:raise ValueError('Verified lesson requires regression test')
        self.rules=value['rules'];self.revision=value['revision']

    def model_client_options(self):
        active=any(r['enabled'] and r.get('review_status')=='verified' and r['handler']=='identity_response_encoding' for r in self.rules)
        return {'default_headers':{'Accept-Encoding':'identity'}} if active else {}

    def check(self,request,env):
        from .outcomes import transport_precheck
        from chinatravel.soft_constraints.scoring import clock
        checks=[];blocking=None
        for rule in self.rules:
            entry={'lesson_id':rule['id'],'handler':rule['handler'],'phase':rule['phase']}
            if not rule['enabled']:entry['status']='disabled'
            elif rule.get('review_status')!='verified':entry['status']='skipped_unreviewed'
            elif rule['phase']=='model_setup':entry['status']='available_at_model_setup'
            else:
                issue=None
                observed={}
                if rule['handler']=='transport_availability':issue=transport_precheck(request,env,observed)
                elif rule['handler']=='appointment_intervals':
                    events=request.get('locked_events',[])
                    for i,a in enumerate(events):
                        for b in events[i+1:]:
                            if a['day']==b['day'] and max(clock(a['start_time']),clock(b['start_time']))<min(clock(a['start_time'])+a['duration_minutes'],clock(b['start_time'])+b['duration_minutes']):
                                issue={'state':'needs_confirmation','reason_code':'overlapping_appointments','message':'已确认预约时段相互重叠，请调整后再规划。','evidence':{'events':[a,b]}}
                entry['status']='blocked' if issue else 'checked_no_block'
                if observed:
                    entry['observations']=observed
                    if not issue and observed.get('unknown_fields'):entry['status']='uncertain_no_block'
                if issue:
                    issue['lesson_ids']=[rule['id']];entry['evidence']=issue
                    if blocking is None:blocking=issue
            checks.append(entry)
        return {'schema_version':'lesson_check_v1','rules_revision':self.revision,'rules_sha256':self.sha256,
                'checks':checks,'blocking':blocking,'policy':'fresh_evidence_only_no_route_blacklist'}


def record_failure(run_dir,status,request=None,*,root=ROOT):
    """One immutable global occurrence; repeated polling of the same failure is idempotent."""
    root=Path(root);run_dir=Path(run_dir);run_dir.mkdir(parents=True,exist_ok=True)
    reason=status.get('reason_code') or status.get('state','worker_failed')
    existing=run_dir/'ERROR.json'
    if existing.exists():
        old=json.loads(existing.read_text(encoding='utf-8'))
        if old.get('reason_code')==reason:return old
    code,stage,category,lesson_ids,diagnosis=INFO.get(reason,INFO['worker_failed'])
    incident_id=uuid.uuid4().hex;relative=Path('result/incidents')/(incident_id+'.json')
    request=request or {};trip=request.get('trip',{})
    known_evidence={k:status[k] for k in ('transport_availability','missing_directions','scope','note','stop_reason','worker_exit',
        'internal_candidates','independent_legal_candidates','issues','unmapped','evidence','selected_failed_checks') if k in status}
    signature=hashlib.sha256(json.dumps([code,status.get('stage',stage),known_evidence.get('stop_reason'),
        known_evidence.get('evidence',{}).get('exception_type')],sort_keys=True).encode()).hexdigest()[:20]
    all_lessons=list(dict.fromkeys(lesson_ids+status.get('lesson_ids',[])))
    record=scrub({'schema_version':'trip_error_v1','incident_id':incident_id,'incident_file':str(relative).replace('\\','/'),
        'occurred_at':datetime.now(timezone.utc).isoformat(),'error_code':code,'reason_code':reason,'stage':status.get('stage',stage),
        'category':category,'message':status['message'],'diagnosis_status':diagnosis,'evidence':known_evidence,
        'request':{'id':request.get('request_id'),'trip':trip,'money_budget':request.get('facts',{}).get('budget')},
        'lesson_ids':all_lessons,'signature':signature,'run_dir':str(run_dir.resolve()),
        'review_status':'known_lesson' if all_lessons and diagnosis=='confirmed' else 'needs_review',
        'next_action':'依证据核对原因；未知原因先复现，经验修订通过验证后才可启用。'})
    destination=root/relative;destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)
    temporary=existing.with_suffix('.tmp');temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(existing)
    return record


def write_lesson_review(root=ROOT):
    """Group occurrences for review, never promote an incident into an active rule."""
    root=Path(root);groups={}
    for path in sorted((root/'result/incidents').glob('*.json')):
        row=json.loads(path.read_text(encoding='utf-8'));groups.setdefault(row['signature'],[]).append(row)
    lines=['# 错误复盘待办','','此文件按发生记录生成；不会自动启用规则或修改LESSONS.md。','',
           '| 错误编号 | 阶段 | 次数 | 已关联lesson | 最近记录 |','|---|---|---:|---|---|']
    for rows in groups.values():
        last=sorted(rows,key=lambda r:r['occurred_at'])[-1]
        lines.append(f"| {last['error_code']} | {last['stage']} | {len(rows)} | {', '.join(last['lesson_ids']) or '待复现分析'} | {last['incident_id']} |")
    output=root/'result/lessons';output.mkdir(parents=True,exist_ok=True)
    path=output/('review_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.md')
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8');return path
