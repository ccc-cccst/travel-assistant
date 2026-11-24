"""Diagnose local changes against stored P007 drafts without calling the API."""
import argparse
import json
from pathlib import Path
import time

from evaluate_tripspec import P007, ROOT, digest, dump, grade, interaction_pass, load, summarize
from chinatravel.personalization.tripspec import ModelReply, evaluate_draft, public_input, resolve_conflicts
from chinatravel.personalization.cli import environment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    manifest=load(args.source/'MANIFEST.json')
    assert manifest['reference_sha256']==digest(P007/'GOLD_DRAFT.jsonl')
    assert manifest['interaction_sha256']==digest(P007/'INTERACTION_GOLD_DRAFT.json')
    assert load(args.source/'SUMMARY.json')['finished']
    env=environment(manifest['data'])
    gold={g['uid']:g for line in (P007/'GOLD_DRAFT.jsonl').read_text(encoding='utf-8').splitlines() if (g:=json.loads(line))}
    interactions={x['id']:x for x in load(P007/'INTERACTION_GOLD_DRAFT.json')['mixed_inputs']}
    rows=[]
    args.out.mkdir(parents=True,exist_ok=False)
    with (args.out/'RESULTS.jsonl').open('w',encoding='utf-8') as stream:
        for line in (args.source/'RESULTS.jsonl').read_text(encoding='utf-8').splitlines():
            r=json.loads(line)
            text,form=public_input(r['public_input'])
            reply=ModelReply(draft=r['draft'],**r['model'])
            begin=time.perf_counter()
            spec=evaluate_draft(text,form,env.support_cities,reply)
            spec['seconds']=round(time.perf_counter()-begin+reply.elapsed_seconds,5)
            mixed=interactions.get(r['case']) if r['cohort']=='mixed' else None
            spec['grading']=grade(gold[r['uid']],spec,mixed=mixed)
            r['variants']['C']=spec
            r.pop('resolution',None)
            if mixed:
                spec['interaction_pass']=interaction_pass(mixed,spec)
                if mixed['scenario']=='冲突':
                    q=next((q for q in spec['issues'] if q['field']==mixed['expected']['field'] and q.get('choices')),None)
                    if q:
                        chosen=next((i for i,a in enumerate(q['choices']) if a['source']['type']=='form'),None)
                        if chosen is not None:
                            result=resolve_conflicts(spec,{q['id']:chosen})
                            r['resolution']={'ready':result['ready'],'spec':result}
            stream.write(json.dumps(r,ensure_ascii=False)+'\n')
            rows.append(r)
    output={'mode':'post_evaluation_replay','source':str(args.source),'api_attempts_in_replay':0,
            'historic_api_timing_included':True,'new_guard_sha256':digest(ROOT/'chinatravel/personalization/tripspec.py'),
            'cases':len(rows),'cohorts':summarize(rows)}
    dump(args.out/'SUMMARY.json',output)
    print(json.dumps(output,ensure_ascii=False))


if __name__=='__main__':
    main()
