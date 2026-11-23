"""Compare the P035 archive to the current planner on exactly its 30 stored inputs."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import audit_scoped_budget_and_regions as old
from chinatravel.personalization.cli import fresh, read, save
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.tripspec import normalize_saved_spec, resolve_conflicts
from chinatravel.personalization.trip_recovery import run_trip_with_recovery
from chinatravel.planning.engine import minutes


def legs(folder):
    view=read(folder/'PLAN_VIEW.json')
    plan=next(a['itinerary'] for a in read(folder/'CANDIDATE_ARCHIVE.json')['archive'] if a['id']==view['recommended_id'])
    transfers=[]
    origin=None
    for day in plan['itinerary']:
        for stop in day['activities']:
            dest=stop.get('position') or stop.get('start')
            length=sum(minutes(l['end_time'])-minutes(l['start_time']) for l in stop['transports'])
            if length:
                transfers.append(dict(day=day['day'],origin=origin,destination=dest,minutes=length,kind=stop['type']))
            origin=stop.get('end') or dest
    return sorted(transfers,key=lambda t:-t['minutes'])[:3]


def run(task):
    baseline, out=task
    case=baseline['case']
    source=old.OLD/'web_live_01'/case
    spec=normalize_saved_spec(read(source/'request.json'))
    reply=old.read_model_layouts(source)
    if spec['ready']:
        snap=old.facts.trip_snapshot(spec)
        layouts=propose_trip_sketches(snap,use_model=reply.draft is not None,provider=lambda *a,**kw:reply)
    else:
        snap=layouts=None
    folder=out/case
    before=baseline['after']
    after=old.invoke(spec,snap,layouts,folder/'direct')
    row={'case':case,'raw_text':spec['raw_text'],'before':before,'after':after,'issues':spec['issues']}
    for label, path in [('before',old.NEW/'paired_02'/case/('common' if baseline['common_input'] else 'after')),
                        ('after',folder/'direct')]:
        if (path/'PLAN_VIEW.json').exists():
            row[label+'_longest_legs']=legs(path)
    if before['state']!='complete' and after['state']!='complete':
        choices={}
        for q in spec['issues']:
            if q.get('input_type')=='date':
                choices[q['id']]='2027-09-15'
            elif q.get('choices'):
                choices[q['id']]=0
        trial=resolve_conflicts(spec,choices) if choices else spec
        run_trip_with_recovery(trial,folder/'followup',old.env,old.facts)
        rec=read(folder/'followup/RECOVERY.json')
        row['followup']={'simulated_choices':choices,'state':read(folder/'followup/STATUS.json')['state'],
                         'restored':rec['restored'],'draft':bool(rec['draft']),
                         'conditional':[a['change'] for a in rec['attempts'] if a.get('view')]}
    save(folder/'COMPARISON.json',row)
    print(case,before['state'],after['state'],row.get('followup',''),flush=True)
    return row


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--cases',nargs='*')
    args=p.parse_args()
    out=fresh(args.out)
    rows=read(old.NEW/'paired_02/SUMMARY.json')
    if args.cases:
        rows=[r for r in rows if r['case'] in args.cases]
    with ProcessPoolExecutor(max_workers=3,initializer=old.init_world) as pool:
        save(out/'SUMMARY.json',list(pool.map(run,[(r,out) for r in rows])))
