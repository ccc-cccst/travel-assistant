"""P006: frozen stratified original-prompt evaluation, with held-out DSL auditing."""
import argparse
import ast
from collections import Counter
import csv
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_trip_recall import read,save,digest,summarize
DATA=ROOT.parent/'ShortTrip/artifacts/data/5e0ee8a'
CORPUS=ROOT.parent/'datasets/ChinaTravel'
SEED='P006-20261120-fixed-original-prompts'


def public(row):
    return {**{k:row[k] for k in ('uid','start_city','target_city','nature_language')},
            'days':int(row['days']),'people_number':int(row['people_number'])}


def key(row):return hashlib.sha256((SEED+row['uid']).encode()).hexdigest()


def tags(row):
    t=row['nature_language']
    patterns={'money':('预算','不超过','费用'),'mode':('飞机','火车','高铁','地铁','出租车'),
              'lodging':('酒店','房间','床房'),'named_place':('博物馆','乐园','故宫','公园','景区','长城'),
              'food':('火锅','菜','餐','美食'),'clock':('点','早上','晚上','下午'),
              'pace':('轻松','特种兵','轻快','多的景点'),'avoid':('不要','不想','不去')}
    return {k for k,words in patterns.items() if any(w in t for w in words)}


def prepare(out):
    if out.exists():raise ValueError('Use a fresh directory')
    out.mkdir(parents=True)
    from chinatravel.personalization.cli import environment
    env=environment(DATA)
    from chinatravel.personalization.request import normalize_request
    names=['easy','medium','human','preference_base50']+[f'preference{i}_base50' for i in range(6)]
    corpus={name:list(csv.DictReader((CORPUS/(name+'.csv')).open(encoding='utf-8-sig',newline=''))) for name in names}
    # Start with a two-day base already included in the published preference50.
    bases={r['uid'] for r in corpus['preference_base50']}
    anchor=min((r for r in corpus['easy'] if r['uid'] in bases and int(r['days'])==2),key=key)
    chosen=[];seen_cities=set();seen_tags=set()
    for tier in ('easy','medium','human'):
        selected=[anchor] if tier=='easy' else []
        for r in selected:seen_cities.add(r['target_city']);seen_tags.update(tags(r))
        # Explicit day strata, including the sole six-day original human prompt.
        for days in sorted({int(r['days']) for r in corpus[tier]}):
            if any(int(r['days'])==days for r in selected):continue
            options=[r for r in corpus[tier] if int(r['days'])==days and r not in selected]
            r=min(options,key=lambda r:(-(4*(r['target_city'] not in seen_cities)+len(tags(r)-seen_tags)),key(r)))
            selected.append(r);seen_cities.add(r['target_city']);seen_tags.update(tags(r))
        while len(selected)<6:
            options=[r for r in corpus[tier] if r not in selected]
            r=min(options,key=lambda r:(-(4*(r['target_city'] not in seen_cities)+len(tags(r)-seen_tags)),key(r)))
            selected.append(r);seen_cities.add(r['target_city']);seen_tags.update(tags(r))
        for r in selected:
            chosen.append((tier,corpus[tier].index(r)+1,r))
    for i in range(6):
        ns=f'preference{i}_base50'
        r=next(r for r in corpus[ns] if r['uid']=='p'+str(i)+anchor['uid'])
        chosen.append((ns,corpus[ns].index(r)+1,r))
    # Selection is now frozen before inspecting conversion or planner outcomes.
    manifest={'seed':SEED,'selection':'6 per easy/medium/human, cover days then new cities/tags; sha256 tiebreak; 6 existing variants of one selected easy base',
        'base_uid':anchor['uid'],'source_files':{n:{'rows':len(rs),'sha256':digest(CORPUS/(n+'.csv'))} for n,rs in corpus.items()},
        'model':'deepseek-flash','temperature':0,'thinking':'disabled','seconds':180,'candidate_target':10,'time_policy':'flexible',
        'rank_control':'same-input first response; actual API costs retained','arms':['baseline','trip_api'],
        'code_sha256':{str(p.relative_to(ROOT)):digest(p) for folder in ('chinatravel','experiments/timing_probe') for p in (ROOT/folder).rglob('*.py')},
        'data_snapshot':{str(p.relative_to(DATA)):digest(p) for p in DATA.rglob('*.csv')},'cases':[]}
    with zipfile.ZipFile(out/'SOURCE_SNAPSHOT.zip','x',zipfile.ZIP_DEFLATED) as z:
        for rel in manifest['code_sha256']:z.write(ROOT/rel,rel)
        z.write(Path(__file__),'tools/evaluate_existing_prompts.py')
        z.write(ROOT/'tools/compare_trip_recall.py','tools/compare_trip_recall.py')
    corpus_audit=[]
    for ns,rows in corpus.items():
        if ns=='preference_base50':continue # already counted in the base tiers
        for row in rows:
            try:
                req=normalize_request(public(row),env)
                result={'ready':req['ready'],'issues':req['issues'],'unmapped':req['unmapped_requirements'],
                        'preferences':req['preferences'],'facts':req['facts'],'locked_events':req['locked_events']}
            except (ValueError,TypeError,KeyError) as error:result={'ready':False,'conversion_error':str(error)}
            corpus_audit.append({'source':ns,'uid':row['uid'],'days':int(row['days']),**result})
    save(out/'CORPUS_PARSE_AUDIT.json',corpus_audit)
    for ns,index,row in chosen:
        label=f'{ns}_{index:03d}'
        inp=public(row);assert inp['nature_language']==row['nature_language']
        save(out/'inputs'/(label+'.json'),inp)
        save(out/'references'/(label+'.json'),row)
        manifest['cases'].append({'case':label,'source':ns,'row_index_1based':index,'uid':row['uid'],
            'origin':row['start_city'],'destination':row['target_city'],'days':int(row['days']),
            'prompt_sha256':hashlib.sha256(row['nature_language'].encode()).hexdigest(),'tags':sorted(tags(row)),
            'prompt':row['nature_language']})
    save(out/'MANIFEST.json',manifest)
    lines=['# P006 原始题目抽样','',manifest['selection'],'']
    for c in manifest['cases']:lines.extend([f"## {c['case']} · {c['uid']}",'',c['prompt'],''])
    (out/'PROMPTS.md').write_text('\n'.join(lines),encoding='utf-8')
    save(out/'PROGRESS.json',{'state':'prepared','completed':0,'total':48})
    print(json.dumps({'cases':len(chosen),'cities':sorted(seen_cities),'days':sorted({c['days'] for c in manifest['cases']}),
        'base_prompt':anchor['nature_language'],'corpus_ready':dict(Counter((r['source'],r['ready']) for r in corpus_audit).__str__() if False else Counter(r['ready'] for r in corpus_audit))},ensure_ascii=False))


def audit_reference(folder,reference):
    from experiments.evaluation.validation import _check_hard_expression,_hard_backend
    pool=read(folder/'planning/pool.json') if (folder/'planning/pool.json').exists() else []
    status=read(folder/'STATUS.json')
    constraints=ast.literal_eval(reference['hard_logic_py'])
    audited={}
    for item in pool:
        checks=[]
        for i,expr in enumerate(constraints):
            try:state,details=_check_hard_expression(expr,item['plan'],_hard_backend())
            except Exception as error:state,details='unknown',{'type':type(error).__name__,'message':str(error)}
            checks.append({'index':i,'expression':expr,'status':state,'details':details})
        audited[item['fingerprint']]={'passes':all(c['status']=='pass' for c in checks),'checks':checks}
    save(folder/'original_prompt_reference_audit.json',audited)
    chosen=audited.get(status.get('selected_fingerprint'),{})
    return {'reference_selected_pass':chosen.get('passes'),'reference_legal_candidates':sum(i['passes'] for i in audited.values()),
            'reference_failed_checks':[c for c in chosen.get('checks',[]) if c['status']!='pass']}


def extra_metrics(folder):
    result={'prefix_diversity':None,'first_day_proposed_sets':0,'first_day_tried_sets':0}
    p=folder/'planning'
    if not (p/'pool.json').exists():return result
    pool=read(p/'pool.json');events=read(p/'day_sets.json')
    result['prefix_diversity']=len({tuple(a['position'] for a in i['plan']['itinerary'][0]['activities'] if a['type']=='attraction') for i in pool})
    result['first_day_proposed_sets']=len({tuple(s) for e in events if e.get('event')=='model_sets' and e['day']==1 for s in e['proposals']})
    result['first_day_tried_sets']=len({tuple(e['attractions']) for e in events if e.get('event')=='set_trial' and e['day']==1})
    return result


def run(out):
    manifest=read(out/'MANIFEST.json')
    assert all(digest(ROOT/p)==sha for p,sha in manifest['code_sha256'].items()),'Planner source changed since sample freeze'
    key_value=os.environ.get('DEEPSEEK_API_KEY') or getpass.getpass('Backend API key: ')
    if not key_value:raise ValueError('Backend credential required')
    from chinatravel.personalization.cli import environment
    environment(DATA)
    results=read(out/'RESULTS.json') if (out/'RESULTS.json').exists() else []
    done={(r['case'],r['arm']) for r in results}
    for index,case in enumerate(manifest['cases']):
        for arm in (['baseline','trip_api'] if index%2==0 else ['trip_api','baseline']):
            label=case['case']+'__'+arm
            if (case['case'],arm) in done:continue
            folder=out/label
            if folder.exists():raise ValueError('Existing unfinished run must be reconciled: '+str(folder))
            save(out/'PROGRESS.json',{'state':'running','current':label,'completed':len(results),'total':48})
            print('START '+label,flush=True)
            cmd=[sys.executable,'-B','-X','utf8',str(ROOT/'personalize.py'),'plan','--input',str(out/'inputs'/(case['case']+'.json')),
                '--out',str(folder),'--candidate-recall',arm,'--seconds','180','--candidates','10']
            child_env={**os.environ,'DEEPSEEK_API_KEY':key_value,'PYTHONPATH':os.pathsep.join(sys.path),
                       'SHORTTRIP_RANK_CONTROL':str(out/'rank_controls'/case['case'])}
            started=time.perf_counter()
            with (out/(label+'.log')).open('w',encoding='utf-8') as log:
                child=subprocess.run(cmd,cwd=ROOT,env=child_env,stdout=log,stderr=subprocess.STDOUT)
            req=read(folder/'request.json') if (folder/'request.json').exists() else {}
            result={'case':case['case'],'source':case['source'],'uid':case['uid'],'arm':arm,'exit':child.returncode,
                'days_requested':case['days'],'outer_wall_seconds':time.perf_counter()-started,
                'conversion':{'ready':req.get('ready'),'unmapped':req.get('unmapped_requirements'),'issues':req.get('issues'),
                              'preferences':req.get('preferences')},**summarize(folder),
                **audit_reference(folder,read(out/'references'/(case['case']+'.json'))),**extra_metrics(folder)}
            results.append(result);save(out/'RESULTS.json',results)
            print(json.dumps({k:result.get(k) for k in ['case','arm','state','first_legal_seconds','independent_legal_candidates','reference_selected_pass']},ensure_ascii=False),flush=True)
    save(out/'PROGRESS.json',{'state':'complete','completed':len(results),'total':48})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','run']);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();{'prepare':prepare,'run':run}[a.command](a.out.resolve())
