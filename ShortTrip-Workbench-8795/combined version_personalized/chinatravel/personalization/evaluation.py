"""Explicit preference evidence and hard guards; scores are engineering proxies."""
import math
from chinatravel.soft_constraints.scoring import duration, clock


def evaluate_preferences(plan, request, catalog):
    acts=[a for d in plan.get('itinerary',[]) for a in d['activities']]
    costs=[a.get('cost') for a in acts]+[l.get('cost') for a in acts for l in a.get('transports',[])]
    known_number=lambda x: isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>=0
    total=sum(costs) if costs and all(known_number(x) for x in costs) else None
    days=[]; evidence={'attraction':[],'restaurant':[],'accommodation':[]}
    for d in plan.get('itinerary',[]):
        legs=[l for a in d['activities'] for l in a.get('transports',[])]
        times=[duration(l) for l in legs]
        walks=[duration(l) for l in legs if l.get('mode')=='walk']
        sightseeing=[duration(a) for a in d['activities'] if a['type']=='attraction']
        days.append({'day':d['day'],'access_walk_minutes':sum(walks) if all(x is not None for x in walks) else None,
                     'transport_minutes':sum(times) if all(x is not None for x in times) else None,
                     'sightseeing_minutes':sum(sightseeing) if all(x is not None for x in sightseeing) else None,
                     'attractions':len(sightseeing)})
        for a in d['activities']:
            kind='restaurant' if a['type'] in ('breakfast','lunch','dinner') else a['type']
            if kind in evidence:
                evidence[kind].append({'day':d['day'],'name':a.get('position'),
                    'duration_minutes':duration(a),'attributes':catalog.attributes(kind,a.get('position'))})
    numeric_sum=lambda key: sum(d[key] for d in days) if all(d[key] is not None for d in days) else None
    hotel_cost=sum(a['cost'] for a in acts if a['type']=='accommodation' and known_number(a.get('cost')))
    food_cost=sum(a['cost'] for a in acts if a['type'] in ('breakfast','lunch','dinner') and known_number(a.get('cost')))
    metrics={'total_cost':total,'lodging_cost':hotel_cost,'food_cost':food_cost,
             'food_cost_share':food_cost/total if total else None,
             'access_walk_minutes':numeric_sum('access_walk_minutes'),
             'city_transit_minutes':numeric_sum('transport_minutes'),
             'sightseeing_minutes':numeric_sum('sightseeing_minutes'),
             'unique_attractions':len({r['name'] for r in evidence['attraction']}),
             'days':days,'inside_attraction_walking':'unknown'}
    violations=[]
    for event in request.get('locked_events',[]):
        matching=[a for d in plan.get('itinerary',[]) if d['day']==event['day'] for a in d['activities']
                  if a.get('type')=='attraction' and a.get('position')==event['name']]
        if not any(a.get('start_time')==event['start_time'] and duration(a) is not None and duration(a)>=event['duration_minutes'] for a in matching):
            violations.append({'field':'appointment','name':event['name'],'day':event['day'],'reason':'missing_or_time_mismatch'})
    intercity=[a for a in acts if a['type'] in ('train','airplane')]
    for direction,modes in request.get('directional_modes',{}).items():
        activity=(intercity[0] if direction=='outbound' else intercity[-1]) if intercity else None
        if activity is None or activity['type'] not in modes:
            violations.append({'field':'directional_mode','direction':direction,'allowed':modes})
    for key,value in request['hard_limits'].items():
        field={'max_daily_access_walk_minutes':'access_walk_minutes','max_daily_transport_minutes':'transport_minutes',
               'max_daily_attractions':'attractions'}[key]
        for d in days:
            if d[field] is None or d[field]>value: violations.append({'field':key,'day':d['day'],'actual':d[field],'limit':value})
    for feature in request.get('required_hotel_features',[]):
        if not evidence['accommodation'] or any(r['attributes']['status']!='known' or feature not in r['attributes']['values'] for r in evidence['accommodation']):
            violations.append({'field':'required_hotel_features','value':feature,'reason':'missing_or_unverified'})
    f=request['facts']
    for field,compare in [('outbound_earliest',lambda a,b:a>=b),('outbound_latest',lambda a,b:a<=b),
                           ('return_arrival_latest',lambda a,b:a<=b)]:
        if not f[field]: continue
        forward=field.startswith('outbound')
        # Intercity start/end fields are station names. Use occurrence order for the round trip.
        transport=[a for a in acts if a['type'] in ('train','airplane')]
        a=transport[0] if forward and transport else transport[-1] if transport else None
        actual=clock(a.get('start_time') if forward else a.get('end_time')) if a else None
        if actual is None or not compare(actual,clock(f[field])): violations.append({'field':field,'actual':actual,'limit':f[field]})
    items=[]; units=max(1,request['trip']['days']); people=max(1,request['trip']['people'])
    def smaller(n,scale): return None if n is None else 1/(1+n/scale)
    for p in request['preferences']:
        dim,target=p['dimension'],p['target']; matches=[]; score=None; status='unknown'; reason=None
        if dim in ('interest','cuisine','hotel_feature','place'):
            kind={'interest':'attraction','cuisine':'restaurant','hotel_feature':'accommodation'}.get(dim)
            rows=evidence[kind] if kind else sum(evidence.values(),[])
            for r in rows:
                a=r['attributes']
                matched=(r['name']==target if dim=='place' else target in a['tags' if dim=='interest' else 'values'])
                if matched and (dim!='interest' or (r['duration_minutes'] or 0)>=30): matches.append(r)
            if matches: score=1.; status='matched'
            elif rows and all(r['attributes']['status']=='known' for r in rows): score=0.; status='unmatched'
            else: reason='missing_activity_or_attributes'
        elif dim=='pace':
            lo,hi={'relaxed':(90,240),'full':(240,420),'balanced':(120,330)}[target]
            if days and all(d['sightseeing_minutes'] is not None and d['transport_minutes'] is not None for d in days):
                # Partial arrival/return days remain visible; this is an average target, not a hard cap.
                busy=sum(d['sightseeing_minutes']+d['transport_minutes'] for d in days)/units
                score=max(0,1-max(lo-busy,0,busy-hi)/max(hi,1)); status='measured'
        elif dim=='mobility':
            if target=='less_walking': score=smaller(metrics['access_walk_minutes'],30*units)
            elif target=='less_transit': score=smaller(metrics['city_transit_minutes'],90*units)
            else:
                legs=[l for a in acts for l in a.get('transports',[])]; times=[duration(l) for l in legs]
                if legs and all(x is not None for x in times): score=sum(t for l,t in zip(legs,times) if l.get('mode') in ('metro','bus','walk'))/max(1,sum(times))
            status='measured' if score is not None else 'unknown'
        elif dim=='focus':
            if target=='cost': score=smaller(total,500*units*people)
            elif target=='lodging_cost': score=smaller(hotel_cost,200*max(1,units-1)) if evidence['accommodation'] else None
            elif target=='food_share': score=metrics['food_cost_share']
            elif target=='more_attractions': score=min(1,len({r['name'] for r in evidence['attraction'] if (r['duration_minutes'] or 0)>=30})/(3*units))
            elif target=='near_restaurants':
                incoming=[duration(l) for a in acts if a['type'] in ('breakfast','lunch','dinner') for l in a.get('transports',[])]
                if incoming and all(x is not None for x in incoming): score=smaller(sum(incoming)/max(1,len(evidence['restaurant'])),20)
            status='measured' if score is not None else 'unknown'
        elif dim=='anchor':
            names={r['name'] for r in sum(evidence.values(),[])}
            distances=[catalog.distance(target,name) for name in names]
            if distances and all(d is not None for d in distances):
                actual=sum(distances)/len(distances)
                score=smaller(actual,3);status='measured'
                matches=[{'anchor':target,'mean_straight_line_km':actual,'distance_basis':'snapshot_coordinates_not_route'}]
            else: reason='missing_anchor_or_entity_coordinates'
        else: reason='no_verified_evaluator'
        if score is not None and p['direction']=='avoid': score=1-score
        items.append({**p,'status':status,'score':score,'evidence':matches,'reason':reason})
    weights=[3 if p['priority']=='high' else 1 for p in items]
    denominator=sum(weights)
    # Unknown entries keep their place in the denominator and are never marked satisfied.
    utility=sum((p['score'] or 0)*w for p,w in zip(items,weights))/denominator if denominator else None
    return {'eligible':not violations,'violations':violations,'utility':utility,'items':items,'metrics':metrics,
            'coverage':{'known':sum(p['score'] is not None for p in items),'total':len(items)},
            'scoring_version':'engineering_proxy_v1'}
