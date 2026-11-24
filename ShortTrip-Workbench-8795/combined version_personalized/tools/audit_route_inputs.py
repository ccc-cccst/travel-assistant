"""Verify that free text and form submissions can run the same route actions."""
import argparse
import json
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import fresh, save


def verify(output, port):
    origin = f'http://127.0.0.1:{port}'
    opener = build_opener(ProxyHandler({}))

    def send(path, payload=None):
        body = None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
        request = Request(origin+path, data=body, headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=35) as reply:
            return json.load(reply)

    def wait(job):
        deadline = monotonic()+90
        while monotonic()<deadline:
            status=send('/trip-planning/job?id='+job)
            if status['state'] not in ('starting','planning'):
                return status
            sleep(.5)
        raise RuntimeError('Local job did not finish')

    def routes(label, job, view, attempt=None):
        report={}
        for action, view_only in [('view',True),('optimize',False)]:
            body={'job_id':job,'candidate_id':view['recommended_id'],'day':2,'view_only':view_only}
            if attempt:
                body['attempt_id']=attempt
            route_job=send('/trip-planning/route',body)['id']
            route=wait(route_job)['route']
            assert route['day']==2 and route['candidate_id']==body['candidate_id']
            assert route['segments'] and route['model_calls']==0
            report[action]={'job':route_job,'segments':len(route['segments']),
                            'result':route['result'],'model_calls':route['model_calls']}
            save(output/label/(action+'.json'),route)
        return report

    inputs={
        'form': {'form':{'start_city':'广州','target_city':'杭州','days':3,'people_number':2}},
        'text': {'raw_text':'我们两个人从广州出发去杭州玩三天。'},
        'text_with_clarification': {'raw_text':'从广州出发去杭州玩3天，要求要去西湖'},
    }
    results={}
    for label,payload in inputs.items():
        spec=send('/tripspec/interpret',payload)
        save(output/label/'INITIAL_SPEC.json',spec)
        qualified={k:spec[k] for k in ('request_id','request_revision')}
        if not spec['ready']:
            number, = [q for q in spec['issues'] if q['field']=='people' and q.get('input_type')=='number']
            spec=send('/tripspec/resolve',{**qualified,'choices':{number['id']:2}})
        assert spec['ready']
        required={k:spec[k] for k in ('request_id','request_revision')}
        job=send('/trip-planning',required)['id']
        status=wait(job)
        selected=status.get('result')
        attempt=None
        if not selected:
            preview=next(a for a in status['recovery']['attempts'] if a.get('view'))
            selected, attempt=preview['view'],preview['id']
        chosen=routes(label,job,selected,attempt)
        after=send('/tripspec/state?id='+spec['request_id'])
        assert after==spec
        core={a['field']:a['value'] for a in spec['items'] if a['field'] in ('origin_city','destination_city','days','people')}
        results[label]={'core':core,'job':job,'preview':attempt,'interpret_calls':spec['model']['attempts'],
                        'routes':chosen,'revision_unchanged_by_routes':True,
                        'url':f"{origin}/tripspec?id={spec['request_id']}&job={job}"}
        save(output/'SUMMARY.json',results)
        print(label, json.dumps(results[label],ensure_ascii=False),flush=True)
    assert results['form']['core']==results['text']['core']==results['text_with_clarification']['core']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8780)
    args=parser.parse_args()
    verify(fresh(args.out),args.port)
