"""Offline diagnosis of recorded candidate recall; no model or planner run."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=ROOT/'result/manual/b709d2ed7c43403e8022d6a6b99735a8')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve()
    if out.exists():raise ValueError('Choose a fresh output directory')
    from chinatravel.personalization.cli import environment,read,save
    env=environment(ROOT.parent/'ShortTrip/artifacts/data/5e0ee8a')
    from chinatravel.personalization.catalog import PreferenceCatalog
    from chinatravel.personalization.policy import PreferencePolicy
    from chinatravel.soft_constraints.scoring import duration
    run=args.run.resolve();request=read(run/'request.json');plan=read(run/'plan.json')
    events=[e for e in read(run/'planning/day_sets.json') if e.get('event')=='model_sets']
    city=request['trip']['destination_city'];catalog=PreferenceCatalog(env,city);policy=PreferencePolicy(request,catalog)
    table=env.attractions.data[city]
    # Match the original collect_poi_info_all visibility before positional IDs.
    import pandas as pd
    price=pd.to_numeric(table['price'],errors='coerce')
    rows=table.loc[price.map(lambda v:math.isfinite(v) and v>=0)].reset_index(drop=True)
    positive=sum(policy.affinity('attraction',row['name'])>0 for row in rows.to_dict('records'))
    summary=[]
    for e in events:
        c=e['context'];start,end=c['start'],c['end'];cards=c['attractions']
        identity_ok=all(rows.iloc[a['id']]['name']==a['name'] for a in cards)
        before={a.get('position') for d in plan['itinerary'] if d['day']<e['day'] for a in d['activities'] if a['type']=='attraction'}
        overlap=[a['name'] for a in cards if a['name'] in before]
        start_dist=[catalog.distance(start,a['name']) for a in cards]
        end_dist=[catalog.distance(end,a['name']) for a in cards]
        summary.append({'day':e['day'],'start':start,'end':end,'available_from':c['available_from'],
            'names':[a['name'] for a in cards],'candidate_types':dict(Counter(a['type'] for a in cards)),
            'id_order_matches_active_table':identity_ok,
            'start_distance_km':{'minimum':min(start_dist),'maximum':max(start_dist)},
            'end_distance_km':{'minimum':min(end_dist),'maximum':max(end_dist)},
            'selected_prefix_visited_overlap':overlap,
            'overlap_basis':'replay_against_selected_plan_prefix_not_exact_historical_search_state'})
    first=events[0]['context'];start,end=first['start'],first['end']
    def value(i):
        name=rows.iloc[i]['name'];a,b=catalog.distance(start,name),catalog.distance(end,name)
        return a+.35*b if a is not None and b is not None else float('inf')
    ordered=sorted(range(len(rows)),key=lambda i:(value(i),i))
    ranks={i:rank+1 for rank,i in enumerate(ordered)}
    replay=policy.recall(rows,ordered,limit=12)
    same=tuple(replay)==tuple(a['id'] for a in first['attractions'])
    markers=[];calls=0
    for query in ['北京路步行街','广州塔','广东省博物馆','陈家祠','沙面','越秀公园']:
        exact=[i for i,r in rows.iterrows() if r['name']==query]
        matches=exact or [i for i,r in rows.iterrows() if query in r['name']]
        for i in matches[:2]:
            r=rows.iloc[i];name=r['name']
            marker={'probe_name':query,'name':name,'type':str(r['type']),'geo_rank':ranks[i],
                    'in_actual_day1_recall':i in [a['id'] for a in first['attractions']],
                    'from_start_straight_line_km':catalog.distance(start,name),
                    'to_end_straight_line_km':catalog.distance(name,end),
                    'interpretation':'coverage_probe_not_human_quality_ground_truth'}
            if len(markers)<4:
                for source,target,time,label in [(start,name,first['available_from'],'airport_to_probe'),(name,end,'16:00','probe_to_hotel')]:
                    route=env.transportation.goto(city,source,target,time,'taxi');calls+=1
                    marker[label]={'queryable':isinstance(route,list),
                        'minutes':sum(duration(x) for x in route) if isinstance(route,list) and all(duration(x) is not None for x in route) else None,
                        'departure':time,'scope':'snapshot_estimate_not_complete_itinerary_feasibility'}
            markers.append(marker)
    pair=[]
    for i,left in enumerate(summary):
        for right in summary[i+1:]:
            if (left['day'],right['day']) in [(2,3),(1,1)]:
                a,b=set(left['names']),set(right['names'])
                pair.append({'left_day':left['day'],'right_day':right['day'],
                             'same_candidate_names':a==b,'jaccard':len(a&b)/len(a|b),
                             'left_end':left['end'],'right_end':right['end']})
    out.mkdir(parents=True)
    result={'source_run':str(run),'model_calls':0,'planner_runs':0,'route_queries':calls,'city':city,
            'city_eligible_attractions':len(rows),'positive_attraction_preference_matches':positive,
            'replay_matches_recorded_first_recall':same,'events':summary,'candidate_overlap':pair,'coverage_probes':markers,
            'caveats':['Only one historical request; not a global quality conclusion.',
                       'Selected-prefix replay is not a trace of every original search state.',
                       'A queryable pair of routes does not prove a complete daily itinerary is legal.',
                       'Probe venues are examples; popularity is not a user preference or quality target.']}
    save(out/'RESULTS.json',result)
    lines=['# P004 候选召回诊断','','2026-11-20。只读真实运行与当前相同快照；模型调用0、未运行新规划器、未修改在线算法。','',
           f'广州可规划且价格有效景点{len(rows)}个。该请求未选择景点兴趣，景点正向偏好匹配{positive}个。按源码距离规则重放首日候选与记录'+('完全一致。' if same else '存在差异，不能直接解释为同版本重放。'),'',
           '| 日期/模型调用 | 起点 | 终点 | 候选数 | 以最终选案前缀回放的已访问占位 |','|---|---|---|---:|---:|']
    for i,e in enumerate(summary):lines.append(f"| 第{e['day']}天/事件{i+1} | {e['start']} | {e['end']} | {len(e['names'])} | {len(e['selected_prefix_visited_overlap'])} |")
    lines+=['','## 首日覆盖探针','','以下地点只是检查覆盖的探针，不是用户必去清单，也不作为质量标准。','',
            '| 地点 | 当前距离排序名次 | 首日入选 | 距机场/公里 | 距酒店/公里 |','|---|---:|---|---:|---:|']
    for m in markers:lines.append(f"| {m['name']} | {m['geo_rank']} | {m['in_actual_day1_recall']} | {m['from_start_straight_line_km']:.1f} | {m['to_end_straight_line_km']:.1f} |")
    lines+=['','## 观察与限制','',
        '第二、三天模型输入候选名称是否完全相同，以及更换首日酒店后的集合重合，见RESULTS.candidate_overlap。',
        '源码在截取候选并询问模型后才过滤visited与其他日期预约地点，可能浪费候选位置。表中占位按最终选案前缀重放，不声称恢复原始搜索每次调用的visited状态。',
        '少量机场→覆盖探针、探针→酒店的出租车查询见RESULTS.coverage_probes。这里只证明可查询接驳及估算时间，不证明全日用餐、营业、偏好和硬条件均可满足。',
        '此轮未证明中心城区一定优于花都、未证明修改距离系数就能改好，也未做新算法的质量/耗时对照。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'out':str(out),'city_eligible_attractions':len(rows),'positive_attraction_preference_matches':positive,
                      'first_recall_reproduced':same,'candidate_overlap':pair,'coverage_probes':markers},ensure_ascii=False))


if __name__=='__main__':main()
