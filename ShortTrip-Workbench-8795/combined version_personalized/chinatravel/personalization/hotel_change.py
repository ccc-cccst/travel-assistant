"""Preview a verified hotel-only revision of a published itinerary."""
from collections import Counter
from copy import deepcopy
from .cli import read, save
from .integrated_planning import _card
from .itinerary_edit import verified_base
from .plan_assessment import evaluate_archive
from .planning_compat import constructor_input, prepare_snapshot_routes, check_snapshot_reference
from .sketch_planning import _digest, _rebuild_day
from chinatravel.planning.engine import Constructor, SearchBudgetExpired, clock, minutes, total_cost


def rebuild_with_meal_window(traveler, day, hotel, locks, facts):
    order = tuple(range(len(day['activities'])))
    feasible = []
    for shift in (0, 15, 30, 45, -15, -30):
        trial = deepcopy(day)
        for meal in trial['activities']:
            if meal['type'] not in ('lunch', 'dinner', 'breakfast'):
                continue
            opening, closing = {'lunch': (660, 839), 'dinner': (1020, 1199),
                                'breakfast': (420, 599)}[meal['type']]
            start, end = minutes(meal['start_time']), minutes(meal['end_time'])
            revised = min(closing, max(opening, start + shift))
            meal.update(start_time=clock(revised), end_time=clock(revised + end - start))
        for prefer_cheaper in (False, True):
            rebuilt = _rebuild_day(traveler, trial, order, hotel, locks, facts, prefer_cheaper=prefer_cheaper)
            if rebuilt:
                feasible.append(rebuilt)
        if len(feasible) >= 4:
            break
    return min(feasible, key=lambda d:total_cost(d['activities']), default=None)


def hotel_preview(source, out, candidate_id, env, facts):
    spec, snapshot = read(source/'request.json'), read(source/'WORLD_FACTS.json')
    check_snapshot_reference(spec, snapshot, facts)
    save(out/'request.json',spec)
    meta=dict(protocol='integrated_planning_v1',stage='hotel_change',request_id=spec.get('request_id'),
              request_revision=spec['request_revision'])
    def finish(state, message, **detail):
        return save(out/'STATUS.json',dict(meta,state=state,message=message,**detail))
    finish('planning','正在核验替换酒店后的整趟行程。')
    selected, before_check = verified_base(source, candidate_id)
    before = before_check['quality_vector']
    stays={a['position'] for d in selected['itinerary']['itinerary'] for a in d['activities'] if a['type']=='accommodation'}
    if len(stays)!=1:
        return finish('no_hotel_change','当前功能适用于全程住同一家酒店的行程。')
    request, unsupported=constructor_input(spec,snapshot=snapshot)
    if unsupported or request.get('required_hotel_names'):
        return finish('no_hotel_change','当前要求指定了住宿或仍有待核实条件，请先调整需求。')
    prepare_snapshot_routes(request,env,facts)
    traveler=Constructor(request,env,24,1)
    original = selected['itinerary']
    old_hotel = next(a['position'] for d in original['itinerary'] for a in d['activities']
                     if a['type'] == 'accommodation')
    anchors = [a['position'] for d in original['itinerary'] for a in d['activities']
               if a['type'] in ('attraction', 'lunch', 'dinner', 'breakfast')]
    eligible_hotels = [h for h in traveler.hotels if h['name'] != old_hotel]
    nearby = sorted(eligible_hotels, key=lambda h: sum(traveler.distance(h['name'], a) for a in anchors))
    cheap = sorted(eligible_hotels, key=lambda h: h['price']*h['_rooms'])
    hotels = {h['name']:h for near, low in zip(nearby[:24],cheap[:24]) for h in (near, low)}
    archive = []
    tried = 0
    failed_days = Counter()
    for hotel in hotels.values():
        tried += 1
        plan = deepcopy(original)
        for day in plan['itinerary']:
            for stop in day['activities']:
                if stop['type'] == 'accommodation':
                    stop.update(position=hotel['name'], price=hotel['price'], cost=hotel['price']*stop['rooms'])
                    if hotel['_bed_count'] is not None:
                        stop['room_type'] = hotel['_bed_count']
                    else:
                        stop.pop('room_type', None)
        built = []
        try:
            for day in plan['itinerary']:
                locks = {(a['place'],a['start_time']) for a in request['appointments'] if a['day']==day['day']}
                rebuilt = rebuild_with_meal_window(traveler, day, hotel['name'], locks, facts)
                if not rebuilt:
                    failed_days[day['day']] += 1
                    break
                built.append(rebuilt)
        except SearchBudgetExpired:
            break
        if len(built) == len(plan['itinerary']):
            plan['itinerary'] = built
            archive.append({'id':_digest(plan),'source':'hotel_change','itinerary':plan})
    trial=dict(schema_version='sketch_trial_v1',snapshot_id=snapshot['snapshot_id'],archive=archive)
    assessment=evaluate_archive(spec,snapshot,trial,env,facts)
    save(out/'PLAN_ASSESSMENT.json',assessment)
    eligible=[r for r in assessment['candidates'] if r['validation_report']['passed'] and
              r['quality_vector']['city_transit_minutes']<before['city_transit_minutes']]
    issues=Counter(i['code'] for c in assessment['candidates'] for i in c['validation_report']['issues'])
    evidence=dict(hotels_tried=tried,rebuilt=len(archive),failed_days=dict(failed_days),rejections=dict(issues))
    if not eligible:
        detail='。有候选超过预算' if any('budget' in k for k in issues) else ''
        return finish('no_hotel_change','本次未找到满足原要求且接驳更短的酒店'+detail+'，可继续使用原方案。',evidence=evidence)
    best=min(eligible,key=lambda r:(r['quality_vector']['city_transit_minutes'],r['quality_vector']['total_cost_cny']))
    chosen=next(r for r in archive if r['id']==best['id'])
    card=_card(chosen,best,spec,facts)
    save(out/'WORLD_FACTS.json',snapshot)
    save(out/'CANDIDATE_ARCHIVE.json',{**trial,'archive':[chosen]})
    save(out/'PLAN_VIEW.json',dict(verified_count=1,recommended_id=card['id'],alternatives=[],candidates=[card]))
    hotel=next(a['name'] for d in card['days'] for a in d['activities'] if a['kind']=='accommodation')
    change=dict(candidate_id=candidate_id,before=before,after=card['metrics'],old_hotel=old_hotel,new_hotel=hotel)
    return finish('complete','换酒店预览已核验。',hotel_change=change,evidence=evidence)
