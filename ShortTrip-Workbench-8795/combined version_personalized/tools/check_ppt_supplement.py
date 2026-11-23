"""Explicitly labelled supplementary diagnostics; never overwrite P070 primary arms."""
import argparse
from functools import partial
import json
from pathlib import Path
import sys
from time import perf_counter

import check_ppt_scenario as trial

def canonical():
    raw=dict(trial.CASES)['appointment'].replace('上海博物馆（人民广场馆）','上海博物馆')
    trial.save(trial.OUT/'CANONICAL_MANIFEST.json',{'raw_text':raw,'why':'Isolate appointment-name grounding failure; this is not a replacement for the original test.'})
    trial.CASES=[('appointment_canonical',raw)]
    trial.workbench('deepseek',tag='_supplement')

def direct(repair=False):
    import compare_planning_models as baseline
    baseline.TOKENS=4096
    baseline.MODEL_TIMEOUT=60
    baseline.SECONDS=60
    provider=partial(baseline.request_deepseek_json,capture_invalid=True)
    env=baseline.environment(baseline.DATA)
    facts=baseline.WorldFacts.configured(baseline.DATA)
    root=trial.OUT/('direct_repair_deepseek' if repair else 'direct_deepseek')
    root.mkdir(exist_ok=False)
    trial.save(root/'MANIFEST.json',{'cases':['basic','preferences','appointment'],'model':'deepseek-flash',
        'facts':'Reuse actual ShortTrip WORLD_FACTS snapshot; model_facts supplies available candidate IDs, hours, prices and ticket tables.',
        'process':'One additional repair with previous draft, actual route timings and verifier feedback; no search.' if repair else 'One model generation; independent routes and validation; no search and no model repair.',
        'max_output_tokens':4096,'model_timeout_seconds':60,'route_budget_seconds':60,
        'limits':'Shares ShortTrip extracted constraints; this compares scheduling only. Does not represent the best prompting or an equal-budget end-to-end benchmark.'})
    summaries=[]
    for name in ('basic','preferences','appointment'):
        out=root/name
        out.mkdir()
        started=perf_counter()
        spec=trial.load(trial.OUT/'shorttrip_deepseek'/name/'SPEC.json')
        source=trial.load(trial.OUT/'shorttrip_deepseek'/name/'row.json')
        archive=trial.ROOT/'result/manual'/source['job']
        snapshot=trial.load(archive/'WORLD_FACTS.json')
        request,unsupported=baseline.constructor_input(spec)
        trial.save(out/'SPEC.json',spec)
        trial.save(out/'WORLD_FACTS.json',snapshot)
        baseline.prepare_snapshot_routes(request,env,facts)
        traveler=baseline.Constructor(request,env,60,1)
        packet,*figures=baseline.model_facts(snapshot,traveler)
        if repair:
            previous=trial.OUT/'direct_deepseek'/name
            packet['previous_draft']=trial.load(previous/'MODEL_REPLY.json')['draft']
            packet['resolved_previous_plan']=trial.load(previous/'CANDIDATE_ARCHIVE.json')['archive'][0]['itinerary']
            packet['verifier_issues']=trial.load(previous/'PLAN_ASSESSMENT.json')['candidates'][0]['validation_report']['issues']
            packet['repair_instruction']='修正上次方案的具体问题。previous_plan 包含真实接驳时刻；火车发车前至少60分钟到站。不能删除原始硬要求。返回完整新方案。'
        trial.save(out/'MODEL_INPUT.json',packet)
        status=baseline.run_direct(spec,snapshot,traveler,packet,figures,provider,out,env,facts)
        summary=baseline.summarize(out,status)
        summary.update(case=name,total_seconds=perf_counter()-started)
        summaries.append(summary)
        trial.save(root/'SUMMARY.json',summaries)
        print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['canonical','direct','repair'])
    args=parser.parse_args()
    canonical() if args.mode=='canonical' else direct(args.mode=='repair')
