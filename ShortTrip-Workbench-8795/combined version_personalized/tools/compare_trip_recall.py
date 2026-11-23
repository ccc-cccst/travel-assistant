"""Sequential paired full-pipeline comparison; credentials never persisted."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(folder):
    status=read(folder/'STATUS.json') if (folder/'STATUS.json').exists() else {'state':'no_status'}
    planning=folder/'planning'
    run=read(planning/'run.json') if (planning/'run.json').exists() else {}
    calls=[json.loads(line) for line in (planning/'requests.jsonl').read_text(encoding='utf-8').splitlines()] if (planning/'requests.jsonl').exists() else []
    phases={}
    for call in calls:
        phase=call.get('phase','unknown');p=phases.setdefault(phase,{'calls':0,'seconds':0,'output_tokens':0})
        p['calls']+=1;p['seconds']+=call.get('seconds',0);p['output_tokens']+=call.get('output_tokens',0) or 0
    pool=read(planning/'pool.json') if (planning/'pool.json').exists() else []
    selected=next((i for i in pool if i['fingerprint']==status.get('selected_fingerprint')),None)
    from chinatravel.soft_constraints.scoring import duration
    acts=[a for d in selected['plan']['itinerary'] for a in d['activities']] if selected else []
    days=[]
    if selected:
        for d in selected['plan']['itinerary']:
            visits=[a for a in d['activities'] if a['type']=='attraction']
            days.append({'day':d['day'],'attractions':[a['position'] for a in visits],
                'visits':[{'name':a['position'],'minutes':duration(a)} for a in visits],
                'sightseeing_minutes':sum(duration(a) or 0 for a in visits)})
    routes={json.dumps([[a['type'],a.get('position')] for d in i['plan']['itinerary'] for a in d['activities']],ensure_ascii=False) for i in pool}
    recall=read(planning/'trip_recall.json') if (planning/'trip_recall.json').exists() else {}
    events=read(planning/'day_sets.json') if (planning/'day_sets.json').exists() else []
    contexts=[e['context'] for e in events if e.get('event')=='model_sets']
    report=status.get('preferences') or {}
    return {'state':status['state'],'selected_legal':status.get('selected_legal'),
        'first_legal_seconds':status.get('first_independent_legal_seconds'),
        'end_to_end_seconds':status.get('end_to_end_seconds'),
        'independent_legal_candidates':status.get('independent_legal_candidates',0),
        'internal_candidates':len(pool),'distinct_activity_routes':len(routes),
        'metrics':report.get('metrics'),'preference_items':report.get('items'),
        'days':days,'short_visits_under_30':sum((duration(a) or 0)<30 for a in acts if a['type']=='attraction'),
        'model_calls':len(calls),'model_seconds':sum(c.get('seconds',0) for c in calls),
        'output_tokens':sum(c.get('output_tokens',0) or 0 for c in calls),'model_phases':phases,
        'run':run,'global_recall':{'state':recall.get('state'),'seconds':recall.get('seconds'),
            'directions':recall.get('directions'),'reason':recall.get('reason'),
            'city_catalog_size':len(recall.get('context',{}).get('attractions',[]))},
        'first_day_recall':[a['name'] for a in contexts[0]['attractions']] if contexts else [],
        'recall_unique_places':len({a['name'] for c in contexts for a in c['attractions']})}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=180)
    parser.add_argument('--candidates',type=int,default=10)
    parser.add_argument('--arms',nargs='+',choices=['baseline','dedupe_only','trip_api'],default=['baseline','trip_api'])
    parser.add_argument('--freeze-rankings',action='store_true',
        help='Use the first response for identical ranking input, retaining actual API time/tokens')
    args=parser.parse_args();output=args.out.resolve()
    key=os.environ.get('DEEPSEEK_API_KEY') or getpass.getpass('Backend API key: ')
    if not key:raise SystemExit('Backend credential required')
    if output.exists():raise SystemExit('Choose a fresh output directory')
    output.mkdir(parents=True)
    os.environ['DEEPSEEK_API_KEY']=key
    inputs={
        'guangzhou':read(ROOT/'result/manual/b709d2ed7c43403e8022d6a6b99735a8/request.json'),
        'museum':read(ROOT/'result/experiments/P001_live_v4/inputs/museum.json'),
        'appointment':read(ROOT/'result/experiments/P001_live_v4/inputs/four_day_appointment.json')}
    hashes={str(p.relative_to(ROOT)):digest(p) for folder in ('chinatravel','experiments/timing_probe')
            for p in (ROOT/folder).rglob('*.py')}
    data=ROOT.parent/'ShortTrip/artifacts/data/5e0ee8a'
    snapshot=[{'path':str(p.relative_to(data)),'bytes':p.stat().st_size,'sha256':digest(p)} for p in data.rglob('*.csv')]
    save(output/'MANIFEST.json',{'model':'deepseek-flash','thinking':'disabled','temperature':0,
        'api_seed_supported':False,'seconds':args.seconds,'candidate_target':args.candidates,
        'time_policy':'flexible','workers':1,'code_sha256':hashes,'data_csv_snapshot':snapshot,
        'arms':args.arms,'rank_answers_controlled':args.freeze_rankings,
        'treatment':'global_recall_plus_pretruncate_visited_filter'})
    for name,request in inputs.items():save(output/'inputs'/(name+'.json'),request)
    results=[]
    for index,name in enumerate(inputs):
        offset=index%len(args.arms)
        arms=args.arms[offset:]+args.arms[:offset]
        for arm in arms:
            label=name+'__'+arm;folder=output/label
            command=[sys.executable,'-B','-X','utf8',str(ROOT/'personalize.py'),'plan',
                '--input',str(output/'inputs'/(name+'.json')),'--out',str(folder),
                '--candidate-recall',arm,'--seconds',str(args.seconds),'--candidates',str(args.candidates)]
            print('START '+label,flush=True)
            started=time.perf_counter()
            child_env={**os.environ,'PYTHONPATH':os.pathsep.join(sys.path)}
            child_env.pop('SHORTTRIP_RANK_CONTROL',None)
            if args.freeze_rankings:child_env['SHORTTRIP_RANK_CONTROL']=str(output/'rank_controls'/name)
            with (output/(label+'.log')).open('w',encoding='utf-8') as log:
                child=subprocess.run(command,cwd=ROOT,env=child_env,
                    stdout=log,stderr=subprocess.STDOUT)
            result={'case':name,'arm':arm,'worker_exit':child.returncode,'outer_wall_seconds':time.perf_counter()-started,
                    **summarize(folder)}
            results.append(result);save(output/'RESULTS.json',results)
            print(json.dumps({k:result[k] for k in ('case','arm','state','selected_legal','first_legal_seconds','internal_candidates','model_calls')},ensure_ascii=False),flush=True)
            if result['state']=='worker_failed':
                raise SystemExit('Worker failed; preserve this run and fix before starting another case.')
    print('DONE '+str(output),flush=True)


if __name__=='__main__':main()
