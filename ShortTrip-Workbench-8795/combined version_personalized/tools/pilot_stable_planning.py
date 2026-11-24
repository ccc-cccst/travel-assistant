"""Reproduce the P009 integration pilot using original P007 requests and the V20 snapshot."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Reuse the previous fast constructor and its independent verifier as the pilot backend.
sys.path.append(str(ROOT.parent / 'ShortTrip'))

from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.city_scope import frozen_city_outcome
from chinatravel.personalization.lessons import record_failure
from chinatravel.personalization.tripspec import interpret
from chinatravel.personalization.world_facts import WorldFacts
from chinatravel.personalization.planning_compat import audit, bind_snapshot_routes, constructor_input
from chinatravel.planning.engine import Constructor
from chinatravel.planning.validation import validate_plan


def cases(cities):
    path = ROOT / 'result/experiments/P007_tripspec_evaluation/run_v5_replay/RESULTS.jsonl'
    remaining = {'easy', 'medium', 'human'}
    for line in path.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        tier = row['case'].split('_')[0]
        spec = row['variants']['C']
        if row['cohort'] == 'holdout' and tier in remaining and spec['ready']:
            remaining.remove(tier)
            yield row['case'], spec
    assert not remaining
    yield 'four_day_appointment', interpret(read(ROOT / 'examples/four_day_appointment.json'), cities)


def proposals(snapshot, request):
    spots = [p for p in snapshot['candidates']['attraction'] if p['routable_in_snapshot']]
    liked = set(request['profile']['interest_types'])
    spots.sort(key=lambda p: not bool(liked.intersection(p['tags']['values'])))
    if not spots:
        return []
    def distance(a, b):
        return sum((a['location'][k] - b['location'][k])**2 for k in ('lat','lon'))
    second = next((p for p in spots[1:] if p['tags']['values'] != spots[0]['tags']['values']), None)
    anchors = [spots[0]] + ([second] if second else [])
    return [{'anchor': a['id'], 'nearby': [p['id'] for p in sorted(spots,key=lambda p:distance(a,p))[:4]]}
            for a in anchors]


def run_case(spec, env, facts, seconds):
    start = perf_counter()
    req, unsupported = constructor_input(spec)
    frozen = frozen_city_outcome({'trip':{'origin_city':req['start_city'],'destination_city':req['target_city']}})
    if frozen:
        return frozen
    if unsupported:
        return {'state':'unsupported_requirement','message':'试跑构造器尚无法逐项核验这些要求。','evidence':{'items':unsupported}}
    snap = facts.snapshot(req['target_city'], req['start_city'], days=req['days'],
                          pins=[a['place'] for a in req['appointments']], limit=20)
    bind_snapshot_routes(env, facts)
    for match in snap['pinned_places']:
        if match['status'] != 'found':
            return {'state':'unresolved_appointment','message':'预约地点需补充唯一实体。','evidence':match}
    sketches = proposals(snap,req)
    names = {p['id']:p['name'] for p in snap['candidates']['attraction']}
    archive, diagnostics, checked = [], [], []
    signatures = set()
    first = None
    for sketch in sketches:
        def consume(plan):
            nonlocal first
            report, quality, places = audit(plan, req, env, facts)
            checked.append(report)
            if not report['passed']:
                return
            signature = tuple(tuple(sorted(p['id'] for p in places if p['day'] == d['day'] and p['id'].startswith('attraction:')))
                              for d in plan['itinerary'])
            if signature in signatures:
                return
            signatures.add(signature)
            if first is None:
                first = perf_counter()-start
            archive.append({'sketch':sketch,'itinerary':plan,'validation':report,'quality':quality,'places':places})
        inputs = {**req,'preferred_attractions':[names[i] for i in sketch['nearby']]}
        if 'budget' in req:
            inputs['search_styles'] = ('cheap', 'budget', 'local')
        generation = Constructor(inputs,env,seconds,2,consume).run()
        diagnostics.append({'metrics':generation['metrics'],'reasons':dict(Counter(str(i.get('reason')) for i in generation['diagnostics']))})
    # A deterministic pilot choice. Full preference-based selection is specified in P009.
    archive.sort(key=lambda c:(-c['quality']['distinct_attractions'],c['quality']['city_transit_minutes'],c['quality']['total_cost_cny']))
    elapsed = perf_counter() - start
    regression = {}
    if archive:
        plan = archive[0]['itinerary']
        tampered = deepcopy(plan)
        ticket = tampered['itinerary'][0]['activities'][0]
        ticket['cost'] += 1
        regression['changed_ticket_cost_rejected'] = not audit(tampered,req,env,facts)[0]['passed']
        if req.get('rooms') and any(a['type'] == 'accommodation' for d in plan['itinerary'] for a in d['activities']):
            tampered = deepcopy(plan)
            stay = next(a for d in tampered['itinerary'] for a in d['activities'] if a['type']=='accommodation')
            stay['rooms'] += 1
            stay['cost'] = stay['rooms'] * stay['price']
            codes = {issue['code'] for issue in validate_plan(tampered,req,env)['issues']}
            regression['wrong_room_count_rejected'] = 'rooms' in codes
        if req.get('required_hotel_features'):
            probe = {**req,'required_hotel_features':[*req['required_hotel_features'],'核验对照设施']}
            codes = {issue['code'] for issue in validate_plan(plan,probe,env)['issues']}
            regression['unevidenced_facility_rejected'] = 'hotel_feature' in codes
        for direction, rules in req.get('ticket_restrictions', {}).items():
            if ['高铁'] not in rules:
                continue
            probe = {**req,'ticket_restrictions':{**req['ticket_restrictions'], direction:[['动车D']]}}
            codes = {issue['code'] for issue in validate_plan(plan,probe,env)['issues']}
            regression[f'wrong_{direction}_rail_class_rejected'] = 'intercity_service' in codes and 'ticket_evidence' not in codes
        if req['appointments']:
            tampered = deepcopy(plan)
            lock = req['appointments'][0]
            acts = tampered['itinerary'][lock['day']-1]['activities']
            acts[:] = [a for a in acts if not (a['type']=='attraction' and a['position']==lock['place'])]
            failed = audit(tampered,req,env,facts)[0]
            regression['deleted_appointment_rejected'] = any(i['code']=='appointment_time' for i in failed['issues'])
        if not all(regression.values()):
            raise AssertionError('候选核验器未识别篡改')
    return {'state':'complete' if archive else 'no_verified_candidate',
            'message':'形成快照下通过核验的候选。' if archive else '在本次试排范围内没有得到通过核验的候选。',
            'data':snap['data'],'sketches':sketches,'archive':archive,'selected_index':0 if archive else None,
            'first_verified_seconds':first,'planning_seconds':elapsed,'model_calls':0,'mutation_checks':regression,
            'validated':checked,'search':diagnostics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--seconds',default=6,type=float)
    parser.add_argument('--case', action='append', help='仅运行指定原题，可重复传入此选项')
    args=parser.parse_args()
    out=fresh(args.out)
    start = perf_counter()
    env=environment(DATA)
    facts=WorldFacts.configured(DATA)
    init=perf_counter()-start
    summary=[]
    for case,spec in cases(env.support_cities):
        if args.case and case not in args.case:
            continue
        t=perf_counter()
        try:
            result=run_case(spec,env,facts,args.seconds)
        except Exception as exc:
            result={'state':'worker_failed','message':f'{type(exc).__name__}: {exc}'}
        folder=out/case
        save(folder/'request.json',spec)
        save(folder/'result.json',result)
        if result['state']!='complete':
            reason = {'unsupported_requirement':'needs_confirmation',
                      'no_verified_candidate':'verification_failed'}.get(result['state'], result['state'])
            record_failure(folder,{**result,'reason_code':reason,'stage':'p009_pilot'})
        row={'case':case,'state':result['state'],'candidates':len(result.get('archive',[])),
             'first_verified_seconds':result.get('first_verified_seconds'),'elapsed_seconds':round(perf_counter()-t,3)}
        summary.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    save(out/'SUMMARY.json',{'initialization_seconds':init,'constructor_seconds_per_sketch':args.seconds,'cases':summary})


if __name__=='__main__':
    main()
