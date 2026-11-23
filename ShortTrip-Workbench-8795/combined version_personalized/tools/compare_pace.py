"""Replay the three-day Hangzhou request and inspect verified pace alternatives."""
import argparse
from copy import deepcopy
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.tripspec import label_tuple
from chinatravel.personalization.trip_recovery import run_trip_with_recovery
from chinatravel.personalization.world_facts import WorldFacts


def summarize(folder):
    recovery = read(folder/'RECOVERY.json')
    preview = next((a['id'] for a in recovery['attempts'] if a.get('view')), None)
    source = folder/(preview or recovery['restored'] or '')
    view = read(source/'PLAN_VIEW.json')
    reports = read(source/'PLAN_ASSESSMENT.json')['candidates']
    assert all(all(r['status']=='passed' for r in c['validation_report']['hard_requirements'])
               for c in reports if c['validation_report']['passed'])
    return {'verified': view['verified_count'], 'compared':len(view['candidates']),
            'search_seconds':read(source/'CANDIDATE_ARCHIVE.json')['search_seconds'],
            'candidates':[{'id':c['id'], 'visits':[sum(a['kind']=='attraction' for a in d['activities'])
                                                  for d in c['days']],
                           'metrics':c['metrics'],'goals':c['goals']} for c in view['candidates']]}


def with_pace(spec,label):
    variant=deepcopy(spec)
    variant['items']=[item for item in variant['items'] if item['field']!='pace']
    if label:
        variant['items'].append(label_tuple('pace','prefer',None,'local_experience','soft',label,{'type':'form'}))
    return variant


def bounded_requests(output,env,facts):
    prefix=ROOT/'result/experiments'
    holdout=prefix/'P037_open_requirements/live_02'
    cases = {'budget_metro':holdout/'medium_99', 'same_day':holdout/'easy_6',
             'fixed_appointment':prefix/'P010_trip_world_snapshot/run_02/four_day_appointment'}
    results={}
    for name,folder in cases.items():
        base=read(folder/'request.json')
        results[name]={}
        for pace,label in [('default',None),('full','充实')]:
            spec=with_pace(base,label)
            trial=output/name/pace
            run_trip_with_recovery(spec,trial,env,facts)
            results[name][pace]=summarize(trial)
            print(name,pace,[c['visits'] for c in results[name][pace]['candidates']],flush=True)
    save(output/'SUMMARY.json',results)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--constraints',action='store_true')
    args=parser.parse_args()
    output=fresh(args.out)
    env=environment(DATA)
    facts=WorldFacts.configured(DATA)
    if args.constraints:
        bounded_requests(output,env,facts)
        raise SystemExit()
    source=ROOT/'result/manual/b816f9a6eea84d438892b43dc3f33d89'
    spec=read(source/'request.json')
    results={'before_full':summarize(source)}
    for pace,label in [('full','充实'),('relaxed','舒缓'),('default',None)]:
        variant=with_pace(spec,label)
        folder=output/pace
        started=perf_counter()
        run_trip_with_recovery(variant,folder,env,facts)
        results[pace]={**summarize(folder),'elapsed_seconds':perf_counter()-started}
        print(pace, [c['visits'] for c in results[pace]['candidates']], flush=True)
    save(output/'SUMMARY.json',results)
