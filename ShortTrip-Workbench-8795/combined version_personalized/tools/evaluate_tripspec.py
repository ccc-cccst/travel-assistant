"""Run P007 with independent references and one shared DeepSeek draft per pair."""
import argparse
from collections import Counter
from copy import deepcopy
import getpass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from statistics import median
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chinatravel.personalization.cli import DATA, environment
from chinatravel.personalization.tripspec import (
    MODEL, PROMPT, MAX_TOKENS, TIMEOUT, ModelReply, canonical, evaluate_draft, extract_api, public_input,
)

P007 = ROOT/'result/experiments/P007_tripspec_evaluation'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def legacy_projection(text, form, env):
    from chinatravel.personalization.request import normalize_request

    start=time.perf_counter()
    try:
        old=normalize_request({**form,'nature_language':text},env)
    except (ValueError, KeyError, LookupError, TypeError) as error:
        return {'items':[],'issues':[{'code':type(error).__name__}], 'ready':False,
                'seconds':time.perf_counter()-start}
    items=[]

    def add(f,v,scope='total_trip',u=None,k='fact',op='eq'):
        if v is not None and v != []:
            items.append(dict(field=f,value=canonical(v),unit=u,scope=scope,strength=k,operator=op))

    trip, facts = old['trip'], old['facts']
    for key,unit in [('origin_city',None),('destination_city',None),('people','person'),('days','day')]:
        add(key,trip[key],u=unit)
    add('budget',facts['budget'],u='CNY/group', k='hard',op='lte')
    add('meal_budget',facts['food_price_limit'],'each_meal','CNY/person/meal','hard','lte')
    add('lodging_budget',facts['hotel_price_limit'],'each_night','CNY/group/night','hard','lte')
    add('room_count',facts['rooms'],'lodging','room','hard')
    if facts.get('room_type') is not None:
        cue=next((e.get('source','') for e in old.get('evidence',[]) if e.get('field')=='room_type'),'')
        token=re.search(r'家庭大床房|双床房|单床房|大床房|单人房|双人间',cue)
        add('room_type',token[0] if token else f'{facts["room_type"]}床','lodging',None,'hard','select')
    if len(facts['intercity_modes'])==1:
        add('intercity_mode',facts['intercity_modes'],'intercity_trip',None,'hard','select')
    for direction, value in old['directional_modes'].items():
        add('intercity_mode',value,direction,None,'hard','select')
    if len(facts['local_modes'])==1:
        add('local_mode',facts['local_modes'],'local_travel',None,'hard','allowed_set')
    for v in facts['must_attractions']:
        add('attraction',v,'local_experience',None,'hard','include')
    for p in old['preferences']:
        dimension,value=p['dimension'],p['target']
        fields={'pace':('pace','local_experience','prefer'), 'cuisine':('cuisine','food','prefer'),
                'interest':('experience_theme','local_experience','prefer'),
                'place':('attraction','local_experience','prefer'),
                'hotel_feature':('hotel_feature','lodging','prefer'),
                'anchor':('near_anchor','local_experience','minimize_distance')}
        focus={'cost':('total_cost','total_trip','minimize'), 'lodging_cost':('lodging_cost','lodging','minimize'),
               'more_attractions':('attraction_count','local_experience','maximize'),
               'food_share':('food_spend_share','food','maximize'),
               'near_restaurants':('restaurant_access_time','food','minimize')}
        target=focus.get(value) if dimension=='focus' else fields.get(dimension)
        if target:
            name,scope,op=target
            add(name,value,scope,None,'soft',op)
    for a in old['locked_events']:
        add('appointment',a,'appointment',None,'hard','include')
    return {'items':items,'issues':old['issues'],'ready':old['ready'],'seconds':time.perf_counter()-start}


NORMAL_VALUES = {
    '不赶': '舒缓','轻松':'舒缓','自由轻松':'舒缓','舒适舒缓':'舒缓','轻量概览':'舒缓',
    '紧凑':'充实','少换乘':'交通便利','本地特色菜':'当地特色菜',
    '双人床':'双人床','出租车':'出租车','市内接驳时间':'市内交通时间',
    '可以带小孩玩的乐园':'儿童乐园',
}
OBJ_FIELDS={'transit_time','restaurant_access_time','attraction_count','food_spend','food_spend_share',
            'lodging_cost','total_cost','food_cost'}
MONEY_FIELDS={'budget','budget_target','lodging_budget','meal_budget'}
BLOCK_CODES={'conflicting_values','clarification','missing_required_field','invalid_field','unverified_requirement'}


def norm_value(v):
    v=canonical(v)
    if isinstance(v,list):
        return sorted((norm_value(x) for x in v),key=str)
    if isinstance(v,str):
        v=re.sub(r'[\s（）()“”《》]', '', v)
        return NORMAL_VALUES.get(v,v)
    return v


def compare_field(g,p,raw):
    if g['field']!=p.get('field') or g['strength']!=p.get('strength'):
        return False
    field=g['field']
    xv,yv=norm_value(g['value']),norm_value(p.get('value'))
    if field in {'intercity_mode','local_mode'}:
        if field=='intercity_mode':
            gs,ps=g['scope'],p.get('scope')
            if gs!=ps and {gs,ps}!={'intercity_trip','round_trip'}:
                return False
        return xv==yv and (p.get('operator') not in {'exclude','avoid'})==(g['operator'] not in {'exclude','avoid'})
    if field in MONEY_FIELDS:
        if g['operator']!=p.get('operator'):
            return False
        if xv==yv and p.get('unit')==g['unit']:
            return True
        return any(m.get('derived_value')==yv and m.get('derived_unit')==p.get('unit') for m in g.get('acceptable_mappings',[]))
    if field in OBJ_FIELDS:
        return g['operator']==p.get('operator')
    if field in ('experience_theme','attraction','cuisine','room_type') and isinstance(xv,str) and isinstance(yv,str):
        if g['operator']=='any_of' or p.get('operator') in ('exclude','avoid'):
            return False
        return xv==yv
    return xv==yv


def reference(g):
    items=deepcopy(g['annotations'])
    raw=g.get('raw_only_evaluation')
    if g['record_no']==124:
        items=[a for a in items if a['field']!='flight_booking_status']
    if raw and raw['field']=='intercity_mode':
        next(a for a in items if a['field']=='intercity_mode')['scope']=raw['stated_scope']
    if raw and raw['field']=='days':
        next(a for a in items if a['field']=='days').update(expected_action='needs_confirmation')
    return items


def grade(ref, result, *, mixed=None):
    truth=reference(ref)
    ready=result.get('ready',False)
    blocked_expected={a['field'] for a in truth if a['expected_action']=='needs_confirmation'}
    base_facts={'origin_city','destination_city','days','people'}
    if mixed and mixed['scenario']=='冲突':
        blocked_expected.add(mixed['expected']['field'])
    hard=[a for a in truth if a['expected_action']=='record' and
          (a['strength']=='hard' or a['field'] in base_facts)]
    soft=[a for a in truth if a['expected_action']=='record' and a['strength']=='soft']
    if mixed and mixed['scenario']=='冲突':
        hard=[a for a in hard if a['field']!=mixed['expected']['field']]
    if mixed and mixed['scenario']=='补充' and mixed['expected']['field']=='budget':
        hard.append(dict(field='budget',strength='hard',operator='lte',value=mixed['expected']['value'],unit='CNY/group',scope='total_trip'))
    predicted=result.get('items',[])
    matched=set()

    def missing(labels):
        output=[]
        for g in labels:
            idx=next((i for i,p in enumerate(predicted) if i not in matched and compare_field(g,p,ref['raw_text'])),None)
            if idx is None:
                output.append(g)
            else:
                matched.add(idx)
        return output

    missing_hard=missing(hard)
    missing_soft=missing(soft)
    extra=[p for i,p in enumerate(predicted) if i not in matched and p.get('strength')=='hard' and
           p.get('field') not in (blocked_expected | ({mixed['expected']['field']} if mixed else set()))]
    actual_blocks={a.get('field') for a in result.get('issues',[]) if a.get('code') in BLOCK_CODES}
    assumptions={a['field'] for a in truth if a['expected_action']=='assumption'}
    service_issue=bool(result.get('model',{}).get('error'))
    return {
        'hard_total':len(hard),'hard_matched':len(hard)-len(missing_hard),
        'soft_total':len(soft),'soft_matched':len(soft)-len(missing_soft),
        'unsafe_ready_proxy':ready and bool(blocked_expected or missing_hard or extra),
        'false_block':not ready and not blocked_expected and not assumptions and not service_issue,
        'clear_case':not blocked_expected and not assumptions,
        'expected_confirmation_fields':sorted(blocked_expected),
        'recognized_confirmation_fields':sorted(blocked_expected&actual_blocks),
        'missing_hard':[a['field'] for a in missing_hard],
        'missing_soft':[a['field'] for a in missing_soft],
        'extra_hard':[a['field'] for a in extra],
        'ready':ready,
    }


def interaction_pass(mixed, result):
    expected=mixed['expected']
    f=expected['field']
    items=[a for a in result['items'] if a['field']==f]
    issues=[q for q in result['issues'] if q['field']==f]
    if mixed['scenario']=='冲突':
        values={str(norm_value(a['value'])) for q in issues for a in q.get('choices',[])}
        return not result['ready'] and values >= {str(norm_value(expected[k])) for k in ('text_value','form_value')}
    m=next((a for a in items if norm_value(a['value'])==norm_value(expected['value'])),None)
    if not result['ready'] or not m:
        return False
    sources={s['type'] for s in [m['source'],*m['additional_sources']]}
    return 'form' in sources and (mixed['scenario']!='一致' or 'original_text' in sources)


def summarize(rows):
    output={}
    for cohort in dict.fromkeys(r['cohort'] for r in rows):
        data=[r for r in rows if r['cohort']==cohort]
        group={'cases':len(data),'attempts':sum(r['model']['attempts'] for r in data),
               'service_errors':dict(Counter(r['model']['error'] for r in data if r['model']['error'])),
               'input_tokens':sum(r['model']['usage'].get('prompt_tokens',0) for r in data),
               'output_tokens':sum(r['model']['usage'].get('completion_tokens',0) for r in data)}
        for arm in ('A','B','C'):
            metrics=[r['variants'][arm]['grading'] for r in data if arm in r['variants']]
            if not metrics:
                continue
            times=sorted(r['variants'][arm]['seconds'] for r in data if arm in r['variants'])
            group[arm]={'n':len(metrics),**{k:sum(int(m[k]) for m in metrics) for k in (
                'hard_total','hard_matched','soft_total','soft_matched','unsafe_ready_proxy','false_block','clear_case','ready')},
                'p50_seconds':round(median(times),3),'p95_seconds':round(times[max(0,(95*len(times)+99)//100-1)],3)}
            if cohort=='mixed' and arm in ('B','C'):
                group[arm]['interaction_pass']=sum(r['variants'][arm]['interaction_pass'] for r in data)
        if cohort=='mixed':
            group['resolved_ready']=sum(r.get('resolution',{}).get('ready',False) for r in data)
        output[cohort]=group
    return output


def run(args):
    env=environment(args.data)
    cities=env.support_cities
    frozen=load(P007/'INPUT_MANIFEST.json')
    gold={g['uid']:g for line in (P007/'GOLD_DRAFT.jsonl').read_text(encoding='utf-8').splitlines() if (g:=json.loads(line))}
    interactions=load(P007/'INTERACTION_GOLD_DRAFT.json')
    assert set(gold)=={r['uid'] for group in frozen['splits'].values() for r in group}
    for group in frozen['splits'].values():
        for row in group:
            assert sha256(gold[row['uid']]['raw_text'].encode()).hexdigest()==row['original_text_sha256']
    api_key=os.environ.get('DEEPSEEK_API_KEY')
    if args.ask_key and not api_key:
        api_key=getpass.getpass('DeepSeek key (hidden): ')
    if not args.offline and not api_key:
        raise SystemExit('DEEPSEEK_API_KEY is unavailable.')
    out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    manifest={'suite':args.suite,'data':str(args.data.resolve()),'offline':args.offline,
              'model':MODEL,'timeout':TIMEOUT,'max_tokens':MAX_TOKENS,'retries':0,
              'temperature':0,'thinking':'disabled','shared_model_drafts':True,
              'reference_sha256':digest(P007/'GOLD_DRAFT.jsonl'),
              'input_manifest_sha256':digest(P007/'INPUT_MANIFEST.json'),
              'interaction_sha256':digest(P007/'INTERACTION_GOLD_DRAFT.json'),
              'prompt_sha256':sha256(PROMPT.encode()).hexdigest(),
              'code_sha256':{p:digest(ROOT/p) for p in ('tools/evaluate_tripspec.py',
                  'chinatravel/personalization/tripspec.py','chinatravel/personalization/request.py',
                  'chinatravel/personalization/_legacy_request.py')},
              'command':sys.argv}
    dump(out/'MANIFEST.json',manifest)
    trials=[]
    splits={'dev':('development',),'main':('holdout','known_regression'),
            'all':('development','holdout','known_regression'),'smoke':()}[args.suite]
    for split in splits:
        for row in frozen['splits'][split]:
            g=gold[row['uid']]
            trials.append((split,g['id'],g,{'raw_text':g['raw_text']},None))
    if args.suite in ('main','all'):
        for x in interactions['mixed_inputs']:
            trials.append(('mixed',x['id'],gold[x['source_uid']],{'raw_text':x['raw_text'],'form':x['form_input']},x))
        for r in range(2):
            for uid in frozen['repeat_holdout_uids']:
                g=gold[uid]
                trials.append(('repeat',f'{g["id"]}_r{r+2}',g,{'raw_text':g['raw_text']},None))
    if args.suite=='smoke':
        g=next(g for g in gold.values() if g['record_no']==7)
        trials.append(('smoke',g['id'],g,{'raw_text':g['raw_text']},None))
    forms=[]
    for fixture in interactions['known_form_fixtures']:
        text,form=public_input(fixture['form_input'])
        spec=evaluate_draft(text,form,cities,ModelReply())
        missing=[x['field'] for x in fixture['expected']
                 if not all(any(a['field']==x['field'] and norm_value(a['value'])==norm_value(v)
                                for a in spec['items']) for v in
                            (x['value'] if isinstance(x['value'],list) and x['field']!='intercity_mode' else [x['value']]))]
        forms.append({'source':fixture['source_file'],'missing':missing,'spec':spec,
                      'pass':spec['ready'] and not missing and spec['model']['attempts']==0})
    dump(out/'FORM_RESULTS.json',forms)
    rows=[]
    with (out/'RESULTS.jsonl').open('w',encoding='utf-8') as resultfile:
        for i,(cohort,case,g,payload,mixed) in enumerate(trials,1):
            text,form=public_input(payload)
            a=legacy_projection(text,form,env)
            a['grading']=grade(g,a,mixed=mixed)
            reply=ModelReply(error='offline_baseline_only') if args.offline else extract_api(text,form,cities,api_key)
            variants={'A':a}
            if not args.offline:
                for arm,checked in [('B',False),('C',True)]:
                    before=time.perf_counter()
                    spec=evaluate_draft(text,form,cities,reply,checked=checked)
                    spec['seconds']=round(time.perf_counter()-before+reply.elapsed_seconds,5)
                    spec['grading']=grade(g,spec,mixed=mixed)
                    if mixed:
                        spec['interaction_pass']=interaction_pass(mixed,spec)
                    variants[arm]=spec
            row={'index':i,'cohort':cohort,'case':case,'uid':g['uid'],'public_input':payload,
                 'model':{k:v for k,v in vars(reply).items() if k!='draft'},'draft':reply.draft,'variants':variants}
            if mixed and mixed['scenario']=='冲突' and 'C' in variants:
                from chinatravel.personalization.tripspec import resolve_conflicts
                spec=variants['C']
                field=mixed['expected']['field']
                expected=norm_value(mixed['expected']['form_value'])
                conflict=next((q for q in spec['issues'] if q['field']==field and q.get('choices')),None)
                if conflict:
                    pick=next((j for j,a in enumerate(conflict['choices']) if norm_value(a['value'])==expected),None)
                    if pick is not None:
                        resolved=resolve_conflicts(spec,{conflict['id']:pick})
                        row['resolution']={'ready':resolved['ready'],'spec':resolved}
            resultfile.write(json.dumps(row,ensure_ascii=False)+'\n')
            resultfile.flush()
            rows.append(row)
            dump(out/'PROGRESS.json',{'completed':i,'total':len(trials),'last_case':case,'last_error':reply.error})
            if i==1 or i%10==0 or i==len(trials):
                print(f'P007 {i}/{len(trials)} {case}: {reply.error or "ok"}',flush=True)
            if reply.error in ('AuthenticationError','PermissionDeniedError','NotFoundError'):
                break
            if not args.offline and len(rows)>=4 and all(r['model']['error'] in
                    ('APIConnectionError','APITimeoutError','RateLimitError') for r in rows[-4:]):
                break
    dump(out/'SUMMARY.json',{'finished':len(rows)==len(trials),'forms_pass':sum(x['pass'] for x in forms),
                            'total':len(trials),'completed':len(rows),'cohorts':summarize(rows)})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=DATA)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--suite',choices=['smoke','dev','main','all'],default='dev')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--ask-key',action='store_true')
    run(parser.parse_args())


if __name__=='__main__':
    main()
