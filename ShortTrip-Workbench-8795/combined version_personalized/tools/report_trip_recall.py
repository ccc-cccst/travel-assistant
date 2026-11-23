"""Build a factual comparison from completed runs, without additional model calls."""
import argparse
from collections import Counter
import json
from pathlib import Path


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();root=args.run.resolve();results=read(root/'RESULTS.json')
    manifest=read(root/'MANIFEST.json')
    table=['# P005 全城模型召回实测','',
        f"每次 {manifest['seconds']:g} 秒，目标 {manifest['candidate_target']} 份候选；官方 deepseek-flash、temperature=0、thinking disabled、单工作进程。",
        '每例两臂需求、模型、快照、合法性检查、候选目标与时限一致；顺序交替。新增全城召回和截断前去重构成本轮联合改动。',
        '','|需求|召回|独立合法候选|首份合法秒|完整流程秒|费用元|接驳步行分|市内接驳分|游览分|景点数|不同地点路线数|模型调用/秒|',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    if manifest.get('rank_answers_controlled'):
        table[3:3]=['本轮固定相同输入的交通/酒店排序回复；各组仍真实调用API，耗时和token均为实际生成成本。新增dedupe_only隔离去重作用。','']
    def fmt(value):return '—' if value is None else f'{value:.2f}' if isinstance(value,float) else str(value)
    for r in results:
        m=r['metrics'] or {}
        row=[r['case'],r['arm'],f"{r['independent_legal_candidates']}/{r['internal_candidates']}",r['first_legal_seconds'],
             r['end_to_end_seconds'],m.get('total_cost'),m.get('access_walk_minutes'),m.get('city_transit_minutes'),
             m.get('sightseeing_minutes'),m.get('unique_attractions'),r['distinct_activity_routes'],
             f"{r['model_calls']} / {r['model_seconds']:.2f}"]
        table.append('|'+ '|'.join(map(fmt,row))+'|')
    comparisons=[]
    coverage=[]
    for case in dict.fromkeys(r['case'] for r in results):
        pair={r['arm']:r for r in results if r['case']==case}
        if 'baseline' not in pair or 'trip_api' not in pair:continue
        b,n=pair['baseline'],pair['trip_api'];deltas={}
        for name in ('total_cost','access_walk_minutes','city_transit_minutes','sightseeing_minutes','unique_attractions'):
            old=(b['metrics'] or {}).get(name);new=(n['metrics'] or {}).get(name)
            deltas[name]=new-old if old is not None and new is not None else None
        def requests(arm):
            return [json.loads(x) for x in (root/(case+'__'+arm)/'planning/requests.jsonl').read_text(encoding='utf-8').splitlines()]
        hashes={arm:{c['phase']:c['request_sha256'] for c in requests(arm) if c['phase'] in ('outbound_ranking','hotel_ranking','return_ranking')} for arm in pair}
        matching={p:hashes['baseline'].get(p)==hashes['trip_api'].get(p) for p in hashes['baseline']}
        controls={arm:{c['request_sha256']:c['rank_control']['used_sha256'] for c in requests(arm) if 'rank_control' in c} for arm in pair}
        shared=set.intersection(*(set(c) for c in controls.values())) if all(controls.values()) else set()
        control_check={'shared_ranking_inputs':len(shared),
            'all_shared_used_answers_equal':all(len({c[key] for c in controls.values()})==1 for key in shared) if shared else None,
            'controlled_inputs_per_arm':{arm:len(c) for arm,c in controls.items()}}
        comparisons.append({'case':case,'deltas_new_minus_baseline':deltas,
            'ranking_answer_control':control_check,
            'rank_prompt_hashes_match':matching,'normalized_request_equal':read(root/(case+'__baseline')/'request.json')==read(root/(case+'__trip_api')/'request.json')})
        table+=['',f'## {case}','',f"原组状态 {b['state']}；新组状态 {n['state']}。同需求核对：{comparisons[-1]['normalized_request_equal']}；交通/酒店排序输入哈希：{matching}。"]
        if manifest.get('rank_answers_controlled'):
            table+=['',f'排序控制核对：{control_check}。不同搜索分支如产生不同输入，则不互相套用回复。']
        for arm,r in pair.items():
            planning=root/(case+'__'+arm)/'planning'
            pool=read(planning/'pool.json')
            events=read(planning/'day_sets.json')
            prefixes=Counter(tuple(a['position'] for a in item['plan']['itinerary'][0]['activities'] if a['type']=='attraction') for item in pool)
            trials={tuple(e['attractions']) for e in events if e.get('event')=='set_trial' and e.get('day')==1}
            model_sets=[e for e in events if e.get('event')=='model_sets' and e.get('day')==1]
            offered={tuple(s) for e in model_sets for s in e['proposals']}
            diagnosis={'case':case,'arm':arm,'distinct_first_day_attraction_prefixes':len(prefixes),
                'prefixes':[{'names':list(k),'candidates':v} for k,v in prefixes.items()],
                'first_day_proposed_sets':len(offered),'first_day_tried_sets':len(trials),
                'proposed_not_tried':[list(s) for s in sorted(offered-trials)]}
            coverage.append(diagnosis)
            table+=['',f'### {arm}','',f"最终选案合法：{r['selected_legal']}；停止原因：{r['run'].get('stop_reason')}；少于30分钟的景点：{r['short_visits_under_30']}。",
                f"[逐日行程]({case}__{arm}/planning/ROUTE.md) · [独立核验]({case}__{arm}/independent_audit.json) · [原始结果]({case}__{arm}/STATUS.json)",'']
            for day in r['days']:
                text=' → '.join(f"{v['name']}({v['minutes']}分)" for v in day['visits']) or '没有景点'
                table.append(f"- 第{day['day']}天：{text}")
            table+=['',f"候选覆盖：{len(pool)}份完整候选包含{len(prefixes)}种首日景点前缀；首日模型提议{len(offered)}种集合，实际进入试排{len(trials)}种。未试排不能视为不可行。"]
            table+=['','偏好细项：']
            for p in r['preference_items'] or []:
                names=[e.get('name') for e in p['evidence'] if isinstance(e,dict) and e.get('name')]
                table.append(f"- {p['dimension']}/{p['target']}：{p['status']}，{fmt(p['score'])}；地点证据 {names}。")
            table+=['',f"首日模型可见：{'、'.join(r['first_day_recall'])}。",
                f"整个搜索共向日集合模型展示 {r['recall_unique_places']} 个不同景点。"]
            global_recall=r['global_recall']
            if global_recall['state'] not in (None,'not_requested','filter_only'):
                raw=read(root/(case+'__'+arm)/'planning/trip_recall.json')
                ids={c[0]:c[1] for c in raw.get('context',{}).get('attractions',[])}
                table+=['',f"全城召回：{global_recall['state']}，{fmt(global_recall['seconds'])}秒，输入{global_recall['city_catalog_size']}个景点。"]
                for group in global_recall['directions'] or []:
                    table.append('- '+group['label']+'：'+'、'.join(ids[i] for i in group['attractions']))
            table+=['','|模型阶段|次数|秒|输出token|','|---|---:|---:|---:|']
            for phase,stats in r['model_phases'].items():
                table.append(f"|{phase}|{stats['calls']}|{stats['seconds']:.2f}|{stats['output_tokens']}|")
    table+=['','## 口径与限制','',
        '- 首份合法时间从规划子进程计时入口开始，包含该进程的模型与搜索；完整流程另含父进程数据准备和候选独立核验。',
        '- 合法以现有硬约束和独立规则为准。参考价/营业时间均为本地快照；没有查询实时余票、指定日期房价和临时闭馆。',
        '- 步行只统计接驳腿，景区内步行未知。偏好分是现有工程代理，不能代替人的整体体验评价。',
        '- 10份完整候选可能只有少数不同地点路线，单列不同地点路线数以揭示时间变体。',
        '- 每例每臂一次，官方API没有固定seed。三对探索实验无法证明普遍提升或无退化；不能仅因景点知名就认定更好。',
        '- 新组同时采用全城模型召回和截断前去重；有dedupe_only时可初步区分机制，没有时不能分离贡献。',
        '- 原默认召回保持baseline；trip_api需显式启用。失败时保留原召回与原合法性检查。']
    (root/'REPORT.md').write_text('\n'.join(table)+'\n',encoding='utf-8')
    (root/'COMPARISONS.json').write_text(json.dumps(comparisons,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (root/'COVERAGE_DIAGNOSIS.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(root/'REPORT.md'),'comparisons':comparisons},ensure_ascii=False))


if __name__=='__main__':main()
