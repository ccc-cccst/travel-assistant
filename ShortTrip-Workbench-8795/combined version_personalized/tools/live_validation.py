"""Receive a backend key on stdin and run isolated real-model checks. Never save it."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chinatravel.personalization.cli import save,read


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--serve-port',type=int)
    args=parser.parse_args();output=args.out.resolve()
    import getpass
    key=os.environ.get('DEEPSEEK_API_KEY') or (getpass.getpass('Backend API key: ') if sys.stdin.isatty() else sys.stdin.readline().strip())
    if not key: raise SystemExit('A backend key is required on stdin or in the environment.')
    os.environ['DEEPSEEK_API_KEY']=key
    if output.exists(): raise SystemExit('Choose a fresh output directory.')
    output.mkdir(parents=True)
    # Do not persist the environment, HTTP headers or exception response bodies.
    from openai import OpenAI
    client=OpenAI(api_key=key,base_url='https://api.deepseek.com',timeout=30,max_retries=0,
                  default_headers={'Accept-Encoding':'identity'})
    started=time.perf_counter()
    try:
        response=client.chat.completions.create(model='deepseek-flash',messages=[{'role':'user','content':'只回复 OK'}],
            max_tokens=8,extra_body={'thinking':{'type':'disabled'}})
        save(output/'API_PREFLIGHT.json',{'ok':True,'seconds':time.perf_counter()-started,'model':response.model})
    except Exception as error:
        import traceback
        info={'ok':False,'type':type(error).__name__,'status':getattr(error,'status_code',None),
              'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)]}
        if isinstance(error,TypeError): info['local_type_error']=str(error).replace(key,'<redacted>')[:300]
        save(output/'API_PREFLIGHT.json',info)
        print(json.dumps({'preflight_failed':type(error).__name__,'http_status':getattr(error,'status_code',None)}),flush=True)
        return
    print('Official model preflight passed.',flush=True)
    simple={'uid':'personalized_live_pair','start_city':'南京','target_city':'上海','days':2,'people_number':2,
            'intercity_modes':['train'],'nature_language':'','profile':{}}
    museum={**simple,'profile':{'interests':['museum']}}
    appointment={'uid':'personalized_live_four_days','start_city':'南京','target_city':'上海','days':4,'people_number':2,
        'intercity_modes':['train'],'nature_language':'','profile':{'interests':['museum'],'pace':'relaxed'},
        'appointments':[{'name':'上海博物馆','day':3,'start_time':'15:00','duration_minutes':60}]}
    results=[]
    for name,request in [('ordinary',simple),('museum',museum),('four_day_appointment',appointment)]:
        source=output/'inputs'/(name+'.json');save(source,request)
        command=[sys.executable,'-B','-X','utf8',str(ROOT/'personalize.py'),'plan','--input',str(source),
                 '--out',str(output/name),'--seconds','120','--candidates','3']
        print('START '+name,flush=True)
        child_env={**os.environ,'PYTHONPATH':os.pathsep.join(sys.path)}
        with (output/(name+'.log')).open('w',encoding='utf-8') as log:
            process=subprocess.run(command,cwd=ROOT,env=child_env,stdout=log,stderr=subprocess.STDOUT)
        status=read(output/name/'STATUS.json') if (output/name/'STATUS.json').exists() else {'state':'failed_before_status'}
        results.append({'case':name,'exit_code':process.returncode,'status':status})
        save(output/'RESULTS.json',results)
        print(json.dumps({'case':name,'state':status['state'],'selected_legal':status.get('selected_legal'),
                          'first_legal_seconds':status.get('first_independent_legal_seconds')}),flush=True)
    if args.serve_port:
        print('Local backend enabled on port '+str(args.serve_port),flush=True)
        child_env={**os.environ,'PYTHONPATH':os.pathsep.join(sys.path)}
        subprocess.run([sys.executable,'-B','-X','utf8','-m','chinatravel.personalization.web','--port',str(args.serve_port)],
                       cwd=ROOT,env=child_env)


if __name__=='__main__':main()
