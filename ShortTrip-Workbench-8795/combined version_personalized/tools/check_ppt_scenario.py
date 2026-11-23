"""Small, evidence-preserving PPT scenario pilot. Never changes planner code."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import functools
import hashlib
import importlib.util
import json
import os
import pickle
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
NATIVE = WORKSPACE / 'ChinaTravel'
DATABASE = ROOT.parent/'ShortTrip/artifacts/data/6de12f4/sandbox_profiles/xiamen12_historical_v20_completion/database'
OUT = ROOT/'result/experiments/P070_ppt_scenario'
BASE = '我们两个人从南京坐高铁往返上海，玩三天，整团总预算不超过6000元。想逛博物馆、品尝本帮菜，第三天返回南京。'
CASES = [
    ('original', '我们两个人从南京出发去上海玩三天，想吃本帮菜、少走路。第二天15:00有一个固定预约，第三天晚上需要返回南京，希望行程不要太赶。'),
    ('basic', BASE),
    ('preferences', BASE+'希望少走路，行程轻松一点。'),
    ('appointment', BASE+'希望少走路，行程轻松一点。第二天15:00至16:00已预约参观上海博物馆（人民广场馆），必须保留这个时段。'),
]

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')

def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def manifest():
    OUT.mkdir(exist_ok=False)
    files=list(DATABASE.rglob('*'))
    hashes={p.relative_to(DATABASE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    save(OUT/'MANIFEST.json',{'purpose':'Select a credible presentation example, not estimate general accuracy',
        'start_date':'2026-11-27','model':'deepseek-flash','native_source':str(NATIVE),
        'native_commit':subprocess.check_output(['git','-C',str(NATIVE),'rev-parse','HEAD'],text=True).strip(),
        'database':str(DATABASE),'database_hashes':hashes,'cases':dict(CASES),
        'native_outer_seconds':180,'native_max_output_tokens':4096,
        'workbench_policy':'Unchanged product limit: search 60s and 15 candidates; client wait 140s',
        'fairness':'Feasibility pilot with same city tables and model; different native route engines, prompts, token limits and product stops. Not an equal-budget speed comparison.',
        'native_adaptations':['absolute data directory binding','DeepSeek connection with thinking disabled','timing and outer-deadline guards'],
        'original_policy':'Interpretation only; unidentified appointment requires clarification. No invented location or duration.'})
    print(str(OUT),flush=True)

def workbench(provider, case_name=None, tag=''):
    path=ROOT.parent/'shorttrip-webui/shorttrip-webui/webui/check_planning.py'
    modspec=importlib.util.spec_from_file_location('workbench_check',path)
    mod=importlib.util.module_from_spec(modspec);modspec.loader.exec_module(mod)
    rows=[]
    for name,raw in CASES:
        if case_name and name != case_name: continue
        target=OUT/('shorttrip_'+provider+tag)/name
        if target.exists():
            raise RuntimeError('Existing run; use new output rather than overwrite')
        case={'case':name,'level':'constructed','raw_text':raw,
              'corpus_core':{'origin_city':'南京','destination_city':'上海','days':3,'people':2}}
        if name=='original':
            target.mkdir(parents=True)
            tick=time.perf_counter()
            spec=mod.call('http://127.0.0.1:8795','interpret',{'raw_text':raw,'form':{'start_date':'2026-11-27'},'model_provider':provider})
            save(target/'SPEC.json',spec)
            row={'case':name,'ready':spec['ready'],'state':'interpretation_only','seconds':time.perf_counter()-tick,'issues':spec.get('issues')}
            save(target/'row.json',row)
        else:
            target.parent.mkdir(exist_ok=True,parents=True)
            row=mod.check('http://127.0.0.1:8795',case,target,'2026-11-27',provider)
        rows.append(row);save(OUT/('shorttrip_'+provider+tag)/'SUMMARY.json',rows)
        print(json.dumps({k:row.get(k) for k in ('case','outcome','ready','state','interpret_s','first_s','final_s','candidate_count','reason')},ensure_ascii=False),flush=True)

class Deadline(BaseException): pass

def export_effective():
    sys.path.insert(0,str(ROOT))
    from chinatravel.personalization.cli import environment,DATA
    env=environment(DATA)
    payload={'tables':{},'poi':env.poi.data}
    for name in ('attractions','restaurants','accommodations'):
        payload['tables'][name]=getattr(env,name).data
    path=OUT/'EFFECTIVE_DATA.pkl'
    if path.exists(): raise RuntimeError('Do not overwrite effective data')
    path.write_bytes(pickle.dumps(payload))
    save(OUT/'EFFECTIVE_DATA.json',{'purpose':'Native rerun uses the same eligible city rows/POIs exposed by the current product, not raw bilingual merged CSVs',
         'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
         'counts':{name:{city:len(df) for city,df in tables.items()} for name,tables in payload['tables'].items()}})

def native(case_name, seconds, tag, effective_data=False, *, query=None, provider='deepseek',
           effective_path=None, output=None):
    raw=dict(CASES)[case_name] if query is None else query['nature_language']
    target=(output or OUT)/('chinatravel_'+provider+tag)/case_name
    target.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter(); end=start+seconds
    qwen = provider == 'qwen'
    model = 'qwen3.5-4b-q8' if qwen else 'deepseek-flash'
    row={'case':case_name,'model':model,'outer_limit_s':seconds,'state':'starting'}
    save(target/'row.json',row)
    def guard():
        if time.perf_counter()>end: raise Deadline()
    try:
        os.chdir(NATIVE)
        sys.path.insert(0,str(NATIVE))
        import chinatravel.environment.language as language
        language.relative_database_path=lambda lang,*parts: str(DATABASE.joinpath(*parts))
        from chinatravel.environment.world_env import WorldEnv
        from chinatravel.agent.llms import OpenAICompatibleLLM
        from chinatravel.agent.nesy_agent.llm_driven_rec import LLMDrivenAgent
        env=WorldEnv(lang='zh')
        if effective_data:
            payload=pickle.loads((effective_path or OUT/'EFFECTIVE_DATA.pkl').read_bytes())
            for name,tables in payload['tables'].items():
                obj=getattr(env,name)
                for city in obj.data:
                    obj.data[city]=tables[city].copy()
                    if hasattr(obj,'type_list_map'): obj.type_list_map[city]=obj.data[city]['type'].unique()
                    if hasattr(obj,'cuisine_list_map'): obj.cuisine_list_map[city]=obj.data[city]['cuisine'].unique()
                if hasattr(obj,'poi'): obj.poi.data=payload['poi']
            env.poi.data=payload['poi'];env.transportation.poi_search.data=payload['poi']
            row['data_adapter']='same effective eligible POI tables and coordinates; native routing unchanged'
        model_args = {'temperature': 0}
        if not qwen:
            model_args['extra_body'] = {'thinking': {'type': 'disabled'}}
        llm=OpenAICompatibleLLM(model,base_url='http://127.0.0.1:18080/v1' if qwen else 'https://api.deepseek.com',
             api_key='local-llama' if qwen else os.environ['DEEPSEEK_API_KEY'],max_tokens=4096,
             default_request_args=model_args)
        llm._client_kwargs.update(timeout=60 if qwen else 35,max_retries=0)
        calls=[];send=llm._send_request
        def instrument(messages,args):
            guard();tick=time.perf_counter();before=(llm.input_token_count,llm.output_token_count)
            entry={'index':len(calls)+1,'phase':row['state'],'messages':messages}
            try:
                response=send(messages,args);entry['response']=response
                return response
            finally:
                entry.update(seconds=time.perf_counter()-tick,input_tokens=llm.input_token_count-before[0],output_tokens=llm.output_token_count-before[1])
                calls.append(entry);save(target/'CALLS.json',calls)
        llm._send_request=instrument
        log=(target/'stdout.log').open('w',encoding='utf-8')
        with log,redirect_stdout(log),redirect_stderr(log):
            agent=LLMDrivenAgent(env=env,backbone_llm=llm,cache_dir=str(target/'cache'),debug=False,lang='zh')
            collect=agent.collect_innercity_transport
            def capture_route(*a,**kw):
                result=collect(*a,**kw)
                if not isinstance(result,list):
                    save(target/'LAST_ROUTE_FAILURE.json',{'args':a,'result':result,'environment':env.results[-1].to_dict()})
                return result
            agent.collect_innercity_transport=capture_route
            for obj,attr in [(agent,'dfs_poi'),(agent,'constraints_validation'),(env.transportation,'goto')]:
                original=getattr(obj,attr)
                def wrapped(*a,_f=original,**kw):
                    guard();return _f(*a,**kw)
                setattr(obj,attr,wrapped)
            if query is None:
                query={'uid':'ppt_p070_'+case_name,'start_city':'南京','target_city':'上海','days':3,'people_number':2,'nature_language':raw,'start_date':'2026-11-27'}
            save(target/'INPUT.json',query)
            translate=agent.translate_nl2sl
            def capture(query,load_cache=False):
                row['state']='translating';tick=time.perf_counter()
                translated=translate(query,load_cache=False)
                save(target/'DSL.json',translated)
                row['translation_s']=time.perf_counter()-tick;row['state']='searching'
                save(target/'row.json',row)
                return translated
            agent.translate_nl2sl=capture
            if case_name=='original':
                capture(query);row['state']='interpretation_only'
            else:
                ok,plan=agent.run(query,load_cache=False)
                save(target/'PLAN.json',plan)
                row.update(state='finished',native_success=bool(ok),has_itinerary=bool(plan.get('itinerary')))
        row.update(input_tokens=llm.input_token_count,output_tokens=llm.output_token_count,model_calls=len(calls),model_seconds=sum(c['seconds'] for c in calls))
    except Deadline:
        row.update(state='outer_timeout')
    except Exception as e:
        row.update(state='error',error=type(e).__name__,message=str(e)[:300])
        (target/'trace.txt').write_text(traceback.format_exc(),encoding='utf-8')
    finally:
        sys.stdout=sys.__stdout__;sys.stderr=sys.__stderr__
        if 'calls' in locals():row.update(model_calls=len(calls),model_seconds=sum(c['seconds'] for c in calls),input_tokens=sum(c['input_tokens'] for c in calls),output_tokens=sum(c['output_tokens'] for c in calls))
        row['elapsed_s']=time.perf_counter()-start;save(target/'row.json',row)
        print(json.dumps(row,ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['init','workbench','native','export_effective'])
    parser.add_argument('--provider',default='deepseek',choices=['deepseek','qwen'])
    parser.add_argument('--case',choices=[c[0] for c in CASES])
    parser.add_argument('--effective-data',action='store_true')
    parser.add_argument('--seconds',type=int,default=180)
    parser.add_argument('--tag',default='')
    args=parser.parse_args()
    if args.mode=='init':manifest()
    elif args.mode=='workbench':workbench(args.provider,args.case,args.tag)
    elif args.mode=='export_effective':export_effective()
    else:native(args.case or 'basic',args.seconds,args.tag,args.effective_data)
