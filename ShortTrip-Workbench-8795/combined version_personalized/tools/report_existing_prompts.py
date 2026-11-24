"""Summarize P006 completed results, preserving absent and reference-failed outputs."""
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_trip_recall import read,save


def fmt(v):return '—' if v is None else f'{v:.1f}' if isinstance(v,float) else str(v)


def main(root):
    rows=read(root/'RESULTS.json') if (root/'RESULTS.json').exists() else []
    manifest=read(root/'MANIFEST.json');audit=read(root/'CORPUS_PARSE_AUDIT.json')
    cases={c['case']:c for c in manifest['cases']}
    summary={};failures=[];details=[];controls=[]
    for arm in ('baseline','trip_api'):
        rr=[r for r in rows if r['arm']==arm];legal=[r for r in rr if r['selected_legal']]
        summary[arm]={'n':len(rr),'states':dict(Counter(r['state'] for r in rr)),
            'current_legal':len(legal),'reference_selected_pass':sum(r['reference_selected_pass'] is True for r in rr),
            'reference_fail_despite_current_legal':sum(r['reference_selected_pass'] is False and bool(r['selected_legal']) for r in rr),
            'timeout':sum(r['run'].get('stop_reason') in ('matched_wall_budget','wall_budget_exceeded') or 'deadline' in str(r['run'].get('stop_reason')) for r in rr),
            'median_first_legal_seconds':statistics.median(r['first_legal_seconds'] for r in legal) if legal else None,
            'total_model_seconds':sum(r['model_seconds'] for r in rr),'model_calls':sum(r['model_calls'] for r in rr),
            'one_prefix_despite_10':sum(r['internal_candidates']>=10 and r['prefix_diversity']==1 for r in rr),
            'some_empty_days':sum(any(d['sightseeing_minutes']==0 for d in r['days']) for r in legal),
            'short_visits_under_30':sum(r['short_visits_under_30'] for r in rr)}
    for r in rows:
        folder=root/(r['case']+'__'+r['arm']);status=read(folder/'STATUS.json')
        row={'case':r['case'],'tier':'preference' if r['source'].startswith('preference') else r['source'],'arm':r['arm'],
            'state':r['state'],'reason':status.get('reason_code'),'current_legal':r['selected_legal'],'reference_legal':r['reference_selected_pass'],
            'first_legal_seconds':r['first_legal_seconds'],'end_to_end_seconds':r['end_to_end_seconds'],
            'candidates':r['internal_candidates'],'candidate_routes':r['distinct_activity_routes'],'first_day_prefixes':r['prefix_diversity'],
            'first_day_proposed':r['first_day_proposed_sets'],'first_day_tried':r['first_day_tried_sets'],
            'stop_reason':r['run'].get('stop_reason'),'search_nodes':r['run'].get('search_nodes'),
            'route_compute_seconds':r['run'].get('stats',{}).get('route_compute',{}).get('inclusive_seconds',0),
            'route_compute_calls':r['run'].get('stats',{}).get('route_compute',{}).get('calls',0),
            'model_calls':r['model_calls'],'model_seconds':r['model_seconds'],'wall_seconds':r['run'].get('wall_seconds'),
            'total_cost':(r['metrics'] or {}).get('total_cost'),'access_walk_minutes':(r['metrics'] or {}).get('access_walk_minutes'),
            'city_transit_minutes':(r['metrics'] or {}).get('city_transit_minutes'),'sightseeing_minutes':(r['metrics'] or {}).get('sightseeing_minutes'),
            'unique_attractions':(r['metrics'] or {}).get('unique_attractions')}
        details.append(row)
        for c in r['reference_failed_checks']:failures.append({'case':r['case'],'arm':r['arm'],**c})
    for case in cases:
        pair={r['arm']:r for r in rows if r['case']==case}
        if len(pair)!=2:continue
        hashes={}
        for arm in pair:
            path=root/(case+'__'+arm)/'planning/requests.jsonl'
            calls=[json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
            hashes[arm]={c['request_sha256']:c['rank_control']['used_sha256'] for c in calls if c.get('rank_control')}
        shared=set(hashes['baseline'])&set(hashes['trip_api'])
        controls.append({'case':case,'shared_inputs':len(shared),'shared_answers_equal':all(hashes['baseline'][s]==hashes['trip_api'][s] for s in shared),
                         'identical_request':read(root/(case+'__baseline')/'request.json')==read(root/(case+'__trip_api')/'request.json')})
    corpus={}
    for source in dict.fromkeys(a['source'] for a in audit):
        aa=[a for a in audit if a['source']==source]
        corpus[source]={'n':len(aa),'not_ready':sum(not a['ready'] for a in aa),'unmapped':sum(bool(a.get('unmapped')) for a in aa),
            'issue_codes':dict(Counter(i['code'] for a in aa for i in a.get('issues',[])))}
    paired=[]
    for case in cases:
        pair={r['arm']:r for r in rows if r['case']==case}
        if len(pair)!=2:continue
        b,n=pair['baseline'],pair['trip_api']
        if not (b['selected_legal'] and n['selected_legal']):continue
        paired.append({'case':case,'reference_both_pass':b['reference_selected_pass'] is True and n['reference_selected_pass'] is True,
            'first_legal_delta':n['first_legal_seconds']-b['first_legal_seconds'],
            **{key:(n['metrics'][key]-b['metrics'][key]) if n['metrics'].get(key) is not None and b['metrics'].get(key) is not None else None for key in ('total_cost','access_walk_minutes','city_transit_minutes','sightseeing_minutes','unique_attractions')}})
    save(root/'SUMMARY.json',{'runs':len(rows),'arms':summary,'corpus':corpus,'states':dict(Counter(r['state'] for r in rows)),
        'paired_deltas_new_minus_old':paired,'controls':controls})
    stratified=[]
    for tier in ('easy','medium','human','preference'):
        for arm in ('baseline','trip_api'):
            group=[d for d in details if d['tier']==tier and d['arm']==arm]
            stratified.append({'tier':tier,'arm':arm,'n':len(group),'current_legal':sum(bool(d['current_legal']) for d in group),
                'reference_legal':sum(d['reference_legal'] is True for d in group),
                'timeouts':sum(d['stop_reason']=='matched_wall_budget' for d in group),
                'states':dict(Counter(d['state'] for d in group))})
    save(root/'STRATIFIED.json',stratified)
    save(root/'REFERENCE_FAILURES.json',failures)
    with (root/'RESULTS_TABLE.csv').open('w',encoding='utf-8-sig',newline='') as f:
        if details:w=csv.DictWriter(f,fieldnames=list(details[0]));w.writeheader();w.writerows(details)
    lines=['# P006 原始prompt扩大评测','',f'已完成{len(rows)}/48次。样本为24条原题，来自18条基础题和同一基础题的6个偏好变体。覆盖型抽样，不能估算全题集成功率。',
        '固定同输入的交通/酒店排序回复，真实API成本照计。180秒/10候选；原文和参考DSL分离，未手工修补解析结果。',
        '', '## 汇总','', '|召回|完成|系统合法选案|原题DSL通过|系统合法但参考未通过|10候选仅1个首日前缀|合法首份中位秒|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for arm,s in summary.items():lines.append('|'+ '|'.join(map(fmt,[arm,s['n'],s['current_legal'],s['reference_selected_pass'],s['reference_fail_despite_current_legal'],s['one_prefix_despite_10'],s['median_first_legal_seconds']]))+'|')
    lines+=['','上述中位数按各组成功案例统计；不同组成功集合可能不同，不据此直接宣称加速。配对差值见SUMMARY.json。',
        '', '## 分层结果','', '|层次|召回|次数|系统合法|参考合法|到时限|','|---|---|---:|---:|---:|---:|']
    for s in stratified:lines.append('|'+ '|'.join(map(fmt,[s['tier'],s['arm'],s['n'],s['current_legal'],s['reference_legal'],s['timeouts']]))+'|')
    lines+=['','## 逐项结果','', '|原题|召回|状态|系统/参考合法|首份秒|费用|步行分|接驳分|游览分|候选/路线/首日前缀|','|---|---|---|---|---:|---:|---:|---:|---:|---|']
    for d in details:
        row=[d['case'],d['arm'],d['state'],f"{d['current_legal']}/{d['reference_legal']}",d['first_legal_seconds'],d['total_cost'],d['access_walk_minutes'],d['city_transit_minutes'],d['sightseeing_minutes'],f"{d['candidates']}/{d['candidate_routes']}/{d['first_day_prefixes']}"]
        lines.append('|'+ '|'.join(map(fmt,row))+'|')
    lines+=['','## 全量解析扫描','', '|题集|文本数|需确认/解析失败|有未映射原文|','|---|---:|---:|---:|']
    for source,s in corpus.items():lines.append(f"|{source}|{s['n']}|{s['not_ready']}|{s['unmapped']}|")
    lines+=['','未映射原文不等于全部是遗漏的硬要求，也可能是礼貌语、重复陈述；需要结合原文和解析产物核对。原题DSL本身也可能比原文更严格或更宽松，未通过需逐项解释。','', '## 原题参考未通过项','']
    for f in failures:
        lines +=[f"### {f['case']} / {f['arm']} / {f['status']}",'','```python',f['expression'],'```','']
    lines+=['## 查看原文和行程','', '[原始prompt、UID与来源](PROMPTS.md) · [清单](MANIFEST.json) · [完整CSV](RESULTS_TABLE.csv) · [参考失败原始记录](REFERENCE_FAILURES.json)','']
    for r in rows:
        path=r['case']+'__'+r['arm']
        lines.append(f"- {path}：[状态]({path}/STATUS.json) · [需求解析]({path}/request.json) · [行程]({path}/planning/ROUTE.md) · [参考核验]({path}/original_prompt_reference_audit.json)")
    (root/'RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'runs':len(rows),'arms':summary,'states':dict(Counter(r['state'] for r in rows))},ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]).resolve())
