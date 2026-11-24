"""Paired, offline P028 generalization audit. Run from the personalized repository."""
import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from hashlib import sha256
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare_planning_regression import sealed_cases, source_hashes
from replay_integrated_planning import P007, RESULTS, select_cases
from chinatravel.personalization import integrated_planning as integrated, trip_sketch
from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.sketch_planning import Constructor
from chinatravel.personalization.trip_world import POSITIVE, matches_tags, preferred_tag_terms
from chinatravel.personalization.tripspec import ModelReply, final_spec, form_items, label_tuple, normalize_saved_spec
from chinatravel.personalization.world_facts import WorldFacts

OLD = RESULTS / 'P027_room_and_relative_date/regression_01'
NEW = RESULTS / 'P028_local_experience/regression_02'
MODEL = RESULTS / 'P024_sealed_planning_acceptance/live_02'
METRICS = ('total_cost_cny', 'city_transit_minutes', 'visit_minutes', 'access_walk_minutes',
           'peak_daily_city_transit_minutes', 'longest_transfer_minutes', 'distinct_attractions')


def digest(value):
    return sha256(value.encode()).hexdigest()


def feature_items(spec, field):
    return [i for i in spec['items'] if i['field'] == field and i['strength'] == 'soft'
            and i['operator'] in POSITIVE]


def confirmed_examples():
    # These point selections transcribe existing requests. Extra scoped items keep
    # the original wording (e.g. outbound only, per person, and popularity).
    group = 'CNY/group'
    def pref(field, value, scope, quote):
        return (field, 'prefer', None, scope, 'soft', value, quote)

    def budget(field, value, unit, scope, quote):
        return (field, 'lte', unit, scope, 'hard', value, quote)

    def outbound(mode, quote):
        return ('intercity_mode', 'select', None, 'outbound', 'hard', mode, quote)

    cases = [
        ('human', 8, '南京', '武汉', 2, 3, ['历史'], '特色美食', {},
         [pref('food_quality','本地人常去','food','本地人常去吃的特色美食')]),
        ('human', 73, '武汉', '北京', 2, 4, ['博物馆','历史人文景点'], '当地美食',
         {'budget':3000}, []),
        ('human1000', 312, '南京', '重庆', 3, 4, ['文化景点'], '火锅',
         {'local_modes':['出租车','地铁'], 'room_type':'大床房'},
         [('budget_target','approx',group,'total_trip','soft',5000,'费用5千'),
          pref('crowd_or_popularity','有名','local_experience','有名的旅游景区')]),
        ('human1000', 367, '北京', '南京', 3, 5, ['历史景点'], '当地美食',
         {'intercity_modes':['飞机'], 'local_modes':['出租车'], 'rooms':1,'room_type':'双床房'},
         [budget('budget',4000,'CNY/person','total_trip','人均预算4000元')]),
        ('human1000', 541, '苏州', '北京', 2, 4, ['历史景点','博物馆'], '当地特色美食',
         {'budget':7000,'room_type':'大床房','local_modes':['地铁','出租车']},
         [outbound('高铁','从苏州坐高铁到北京'),
          budget('lodging_budget',350,'CNY/group/night','each_night','每晚的住宿不超过350元'),
          ('dish','accept',None,'food','fact','辣','可以吃辣')]),
        ('human1000', 641, '深圳', '成都', 2, 5, ['历史景点','博物馆'], '当地美食',
         {'intercity_modes':['飞机'],'room_type':'大床房'},
         [budget('budget',6000,'CNY/person','total_trip','预算人均6000')]),
        ('human1000', 666, '成都', '苏州', 2, 5, ['园林'], '当地美食',
         {'budget':10000,'room_type':'大床房','profile':{'pace':'舒缓'}},
         [outbound('飞机','从成都坐飞机到苏州'),
          pref('local_mode','地铁','local_travel','主要交通以地铁为主')]),
    ]
    result = {}
    for src, idx, origin, city, people, days, themes, food, form, extras in cases:
        with (ROOT.parent / 'datasets/ChinaTravel' / (src+'.csv')).open(encoding='utf-8-sig', newline='') as f:
            row = list(csv.DictReader(f))[idx-1]
        text = row['nature_language']
        form = {**form, 'start_city':origin,'target_city':city,'days':days,'people_number':people,
                'profile':{**form.get('profile',{}),'interests':themes,'cuisines':[food]}}
        items = form_items(form)
        for field, op, unit, scope, strength, value, quote in extras:
            start = text.index(quote)
            items.append(label_tuple(field, op, unit, scope, strength, value,
                                     {'type':'original_text','text':quote,'start':start,'end':start+len(quote)}))
        result[f'{src}_{idx}'] = final_spec(text, form, items, [], ModelReply())
    return result


def cohorts():
    original = sealed_cases()
    data = [json.loads(line) for line in P007.read_text(encoding='utf8').splitlines()]
    specs = {r['case']:r['variants']['C'] for r in data if r['cohort'] in ('holdout','known_regression')}
    gold = {g['id']:g for line in (P007.parent.parent/'GOLD_DRAFT.jsonl').read_text(encoding='utf8').splitlines()
            if (g:=json.loads(line))}
    excluded = {gold[k]['family_uid'] for k in set(original) | select_cases('frozen30').keys()}
    new = {}
    for level in ('easy','medium','human'):
        choices = sorted((k for k in specs if k.startswith(level+'_') and gold[k]['family_uid'] not in excluded),
                         key=lambda k:digest('P029-generalization-v1/'+gold[k]['family_uid']))
        for k in choices:
            family = gold[k]['family_uid']
            if family in excluded:
                continue
            excluded.add(family)
            new[k] = specs[k]
            if sum(key.startswith(level+'_') for key in new)==10:
                break
    assert len(new)==30
    return {'calibration':{k:original[k] for k in ('human_37','human_147','easy_185')},
            'independent':new, 'confirmed':confirmed_examples(),
            'mixed_fixture':{'M11':next(r['variants']['C'] for r in data if r['case']=='M11')},
            'model_replay':original}


def old_normalization(spec):
    normalized = normalize_saved_spec(spec)
    previous = {json.dumps(i['source'],sort_keys=True):i['value'] for i in feature_items(spec,'pace')
                if isinstance(i['value'],str) and i['value'] in ('不要太赶','不想太赶')}
    return {**normalized,'items':[{**i,'value':previous.get(json.dumps(i['source'],sort_keys=True),i['value'])}
                                 if i['field']=='pace' else i for i in normalized['items']]}


def outcome(folder):
    status=read(folder/'STATUS.json')
    result={'state':status['state'],'reason':status.get('reason_code') or status.get('error_code'),
            'seconds':status['elapsed_seconds'],'first_seconds':status.get('first_visible_seconds'),
            'model_calls':(status.get('model') or {}).get('requests',0),'sketch_counts':{},'sources':{},
            'count':0,'pick':None}
    if (folder/'TRIP_SKETCHES.json').exists():
        sketches=read(folder/'TRIP_SKETCHES.json')
        result['sketch_counts']=dict(Counter(r['source'] for r in sketches['sketches']))
        result['model_errors']=sketches['model'].get('error')
    if (folder/'WORLD_FACTS.json').exists():
        world=read(folder/'WORLD_FACTS.json')
        matches={}
        for field,kind in [('experience_theme','attraction'),('cuisine','restaurant')]:
            items=feature_items({'items':world['requirements']},field)
            vals=[v for item in items for v in (item['value'] if isinstance(item['value'],list) else [item['value']])
                  if isinstance(v,str)]
            terms=preferred_tag_terms(field,vals,world['destination'])
            hits=[r['name'] for r in trip_sketch._records(world).values() if r['kind']==kind and r['location']
                  and matches_tags(r['tags']['values'],*terms)]
            matches[field]={'values':vals,'matches':hits}
        result['recall']=matches
    if (folder/'PLAN_ASSESSMENT.json').exists():
        a=read(folder/'PLAN_ASSESSMENT.json')
        good=[c for c in a['candidates'] if c['validation_report']['passed']]
        result.update(count=len(good),sources=dict(Counter(c['source'] for c in good)),
                      eligible_ids=[c['id'] for c in good])
        assert len(good)==a['verified_count']
        pick=next((c for c in good if c['id']==a['selection']['recommended_id']),None)
        if pick:
            result['pick']={'id':pick['id'],'source':pick['source'],
                            'goals':[(g['field'],g['value'],g['status']) for g in pick['goals']],
                            **{k:pick['quality_vector'][k] for k in METRICS}}
    return result


def run_one(folder,cid,spec,group,enabled,env,facts):
    with ExitStack() as controls:
        if not enabled:
            controls.enter_context(patch.object(integrated,'normalize_saved_spec',old_normalization))
            controls.enter_context(patch.object(trip_sketch,'_local_experience_layout',return_value=None))
            init=Constructor.__init__
            def without_window(self,request,*args,**kwargs):
                return init(self,{k:v for k,v in request.items() if k!='prefer_activity_windows'},*args,**kwargs)
            controls.enter_context(patch.object(Constructor,'__init__',without_window))
        provider=None
        if group=='model_replay':
            archived_path=MODEL/cid/'model_sketch/TRIP_SKETCHES.json'
            if archived_path.exists():
                model=read(archived_path)['model']
                def provider(*args,**kwargs):
                    return ModelReply(draft=model['draft'],error=model['error'],model=model['name'])
            else:
                def provider(*args,**kwargs):
                    return ModelReply(error='no_archived_response')
        integrated.run_trip_plan(spec,folder,env,facts,model=provider is not None,sketch_provider=provider,
                                 seconds=24,candidates=6)
    return outcome(folder)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--groups',nargs='+')
    args=parser.parse_args()
    groups=cohorts()
    if args.groups:
        groups={k:groups[k] for k in args.groups}
    out=fresh(args.out)
    save(out/'MANIFEST.json',{'data':str(DATA),'seconds':24,'candidate_limit':6,'source_hashes':source_hashes(),
          'audit_script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
          'groups':{g:{k:{'raw_sha256':digest(s['raw_text']),'spec_sha256':digest(json.dumps(s,sort_keys=True)),
                          'source_case':k} for k,s in cases.items()} for g,cases in groups.items()}})
    env=environment(DATA)
    facts=WorldFacts.configured(DATA)
    rows=[]
    for group,cases in groups.items():
        for idx,(cid,spec) in enumerate(cases.items()):
            row={'group':group,'id':cid,'order':['off','on'] if idx%2==0 else ['on','off']}
            for arm in row['order']:
                row[arm]=run_one(out/group/cid/arm,cid,spec,group,arm=='on',env,facts)
            if group=='calibration':
                for arm,path in [('off',OLD),('on',NEW)]:
                    ref=read(path/cid/'PLAN_VIEW.json')
                    assert row[arm]['pick']['id']==ref['recommended_id'] and row[arm]['count']==ref['verified_count'],(cid,arm)
            rows.append(row)
            save(out/'PAIRS.json',rows)
            print(group,cid,[(row[a]['state'],row[a]['count']) for a in row['order']],flush=True)


if __name__=='__main__':
    main()
