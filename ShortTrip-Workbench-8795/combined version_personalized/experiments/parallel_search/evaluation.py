"""Post-run legality, route diversity, and separate quality/time measurements."""
import csv
import json
from pathlib import Path
import time

from .pool import save


def duration(activity):
    def minute(clock):
        hour,part=map(int,clock.split(':'))
        return hour*60+part
    return (minute(activity['end_time'])-minute(activity['start_time']))%1440


def day_sets(plan):
    return tuple(tuple(sorted({a['position'] for a in day['activities'] if a['type']=='attraction'}))
                 for day in plan['itinerary'])


def quality(plan):
    acts=[a for day in plan['itinerary'] for a in day['activities']]
    legs=[leg for activity in acts for leg in activity.get('transports',[])]
    tours=[a for a in acts if a['type']=='attraction']
    return {'reference_cost':round(sum(a['cost'] for a in acts+legs),2),
        'walk_minutes':sum(duration(leg) for leg in legs if leg['mode']=='walk'),
        'local_transit_minutes':sum(duration(leg) for leg in legs),
        'tour_minutes':sum(duration(a) for a in tours),
        'short_visits_below30':sum(duration(a)<30 for a in tours),
        'attractions':len({a['position'] for a in tours})}


def pareto(rows):
    """Keep unique objective vectors; identical metrics do not inflate choices."""
    unique={}
    for row in rows:
        q=row['quality']
        vector=(q['reference_cost'],q['walk_minutes'],q['local_transit_minutes'],-q['tour_minutes'])
        unique.setdefault(vector,row['fingerprint'])
    return [fingerprint for vector,fingerprint in unique.items()
            if not any(other!=vector and all(a<=b for a,b in zip(other,vector)) for other in unique)]


def snapshot(rows,seconds):
    available=[r for r in rows if r['independent_legal'] and r['received_seconds']<=seconds]
    best={}
    for field in ('reference_cost','walk_minutes','local_transit_minutes','tour_minutes'):
        if available:
            chosen=(max if field=='tour_minutes' else min)(available,key=lambda r:r['quality'][field])
            best[field]={'value':chosen['quality'][field],'candidate':chosen['fingerprint']}
        else:
            best[field]=None
    return {'seconds':seconds,'independent_legal':len(available),
        'distinct_daily_attraction_sets':len({r['daily_attraction_sets'] for r in available}),
        'pareto_distinct_vectors':len(pareto(available)),'best_by_dimension':best}


def evaluate(out,bundle,*,audit_query=None):
    from experiments.evaluation.validation import validate_plan
    out=Path(out)
    started=time.perf_counter()
    candidates=json.loads((out/'CANDIDATES.json').read_text(encoding='utf-8'))
    policy=bundle['query'].get('time_policy','flexible')
    gold=json.loads(Path(audit_query).read_text(encoding='utf-8-sig')) if audit_query else None
    audits,rows={},[]
    for item in candidates:
        checks={'translated':validate_plan(item['plan'],bundle['query'],config={'time_policy':policy})}
        if gold is not None:
            checks['dataset']=validate_plan(item['plan'],gold,config={'time_policy':policy})
        valid=all(check['status']=='valid' for check in checks.values())
        audits[item['fingerprint']]=checks
        rows.append({**{k:v for k,v in item.items() if k!='plan'},'independent_legal':valid,
                     'daily_attraction_sets':day_sets(item['plan']),'quality':quality(item['plan'])})
    budget=json.loads((out/'INPUT.json').read_text(encoding='utf-8'))['seconds']
    times=sorted({min(t,budget) for t in (10,20,40,60,180,360,budget)})
    legal=sorted((r for r in rows if r['independent_legal']),key=lambda r:r['received_seconds'])
    result={'system_unique_candidates':len(rows),'independent_legal':len(legal),
        'independent_rejected_or_unknown':len(rows)-len(legal),
        'distinct_daily_attraction_sets':len({r['daily_attraction_sets'] for r in legal}),
        'pareto_candidate_ids':pareto(legal),
        'time_to_independent_legal_k':{str(k):legal[k-1]['received_seconds'] if len(legal)>=k else None for k in (1,5,10,20)},
        'availability_and_quality':[snapshot(rows,t) for t in times],
        'final_best_by_dimension':snapshot(rows,budget)['best_by_dimension'],
        'postprocess_seconds':time.perf_counter()-started,
        'selection_feedback_to_search':False,'dataset_audit_requested':gold is not None}
    save(out/'candidate_audits.json',audits)
    save(out/'candidate_metrics.json',rows)
    save(out/'COLLECTION_METRICS.json',result)
    if rows:
        flattened=[{**{k:v for k,v in row.items() if k not in ('quality','daily_attraction_sets')},
                    'daily_attraction_sets':json.dumps(row['daily_attraction_sets'],ensure_ascii=False),**row['quality']} for row in rows]
        with (out/'CANDIDATE_METRICS.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(flattened[0]))
            writer.writeheader();writer.writerows(flattened)
    return result
