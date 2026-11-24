"""Full real-data search with recorded proposal IDs; no API and no speed claim."""
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'experiments/timing_probe'),str(ROOT/'experiments/c_only')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(); out=args.out.resolve()
    if out.exists(): raise ValueError('Use fresh output')
    from chinatravel.personalization.cli import environment,read,save
    from chinatravel.personalization.request import normalize_request
    from chinatravel.personalization.compiler import compile_request
    env=environment(ROOT.parent/'ShortTrip/artifacts/data/5e0ee8a')
    query=read(ROOT.parent/'combined version_1/experiments/c_only/queries/easy_1.json')
    request=normalize_request({**query,'profile':{'interests':['museum']}},env)
    source=ROOT.parent/'combined version_1/artifacts/product/EXP031_full_factorial/run/easy_1__api__baseline/pool.json'
    pool=read(source)
    chosen=next(p['plan'] for p in pool if any('规划展览馆' in a.get('position','') for d in p['plan']['itinerary'] for a in d['activities']))
    acts=[a for d in chosen['itinerary'] for a in d['activities']]
    services=[a['TrainID'] for a in acts if a['type']=='train']
    hotel=next(a['position'] for a in acts if a['type']=='accommodation')
    import chinatravel.agent.llms as backend
    from chinatravel.agent.llms import OpenAICompatibleLLM

    def factory(*_a,**_kw):
        model=OpenAICompatibleLLM('fixture',name='fixture-recorded-ids',base_url='http://127.0.0.1:1/v1',api_key='fixture')
        def send(messages,kwargs):
            text=messages[-1]['content']
            if '最多六套' in text:
                context=json.loads(text[text.index('{"request"'):])
                daily=next(d['activities'] for d in chosen['itinerary'] if d['day']==context['day'])
                wanted={a.get('position') for a in daily if a['type']=='attraction'}
                ids=[c['id'] for c in context['attractions'] if c['name'] in wanted]
                foods={a.get('position') for a in daily if a['type'] in ('lunch','dinner')}
                response=json.dumps({'sets':[{'attractions':ids}],'restaurants':[c['id'] for c in context['restaurants'] if c['name'] in foods]})
            elif 'HotelNameList' in text: response='HotelNameList: '+json.dumps([hotel],ensure_ascii=False)
            else: response='IDList: '+json.dumps(services)
            return {'content':response}
        model._send_request=send
        return model
    backend.create_llm=factory
    out.mkdir(parents=True)
    from chinatravel.personalization.lessons import LessonBook
    lesson_check=LessonBook().check(request,env)
    assert lesson_check['blocking'] is None,lesson_check
    save(out/'lessons_applied.json',lesson_check)
    save(out/'MANIFEST.json',{'model_backend':'fixture-recorded-ids','api_calls':0,'real_data_search':True,
                              'historical_plan_source':str(source),'not_a_live_model_quality_or_latency_test':True})
    save(out/'request.json',request); query_path=out/'input.json'; save(query_path,compile_request(request)['legacy_query'])
    import run_activity_granularity as probe
    probe.measure(SimpleNamespace(arm='personalized',query=query_path,out=out/'planning',seconds=120,
                                candidate_limit=2,endpoint='http://127.0.0.1:1/v1',time_policy='flexible'))
    from experiments.evaluation.validation import validate_plan
    from chinatravel.personalization.evaluation import evaluate_preferences
    plan=read(out/'planning/plan.json')
    audit=validate_plan(plan,compile_request(request)['legacy_query'],config={'time_policy':'flexible'})
    report=evaluate_preferences(plan,request,__import__('chinatravel.personalization.catalog',fromlist=['PreferenceCatalog']).PreferenceCatalog(env,'重庆'))
    save(out/'VALIDATION.json',{'audit':audit,'personalization':report})
    assert all(c['status'] in ('pass','not_applicable') for c in audit['checks']),audit
    assert report['eligible'] and any(i['status']=='matched' for i in report['items']),report
    print(json.dumps({'verified':True,'api_calls':0,'out':str(out)},ensure_ascii=False))


if __name__=='__main__': main()
