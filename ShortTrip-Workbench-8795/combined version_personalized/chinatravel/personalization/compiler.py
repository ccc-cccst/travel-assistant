"""Compile confirmed fields with existing safe templates; never execute references."""
from copy import deepcopy
from .request import fingerprint


def compile_request(request, allow_issues=False):
    from chinatravel.agent.nesy_agent.constraint_compiler import template_programs
    from chinatravel.agent.nesy_agent.extraction_alignment import derived_fields, literal
    if not request['ready'] and not allow_issues: raise ValueError('需求有待确认或无法核验的硬要求')
    t=request['trip']; f=request['facts']
    q={'uid':request['request_id'],'start_city':t['origin_city'],'target_city':t['destination_city'],
       'days':t['days'],'people_number':t['people'],'nature_language':request['raw_text'],
       'appointments':deepcopy(request['locked_events']),'request_v1':deepcopy(request)}
    codes=[f'{k}=={v}' for k,v in derived_fields(q).items()]
    for field,symbol in [('budget','cost'),('hotel_price_limit','hotel_price'),('food_price_limit','food_price'),('rooms','rooms'),('room_type','room_type')]:
        if f[field] is not None: codes.append(f'{symbol}{"==" if field in ("rooms","room_type") else "<="}{f[field]!r}')
    if set(f['intercity_modes'])!={'train','airplane'}: codes.append('intercity_transport=='+literal(f['intercity_modes']))
    if set(f['local_modes'])!={'metro','taxi','walk'}: codes.append('transport_type<='+literal(f['local_modes']))
    if f['return_departure_latest']: codes.append('return_start_time<='+repr(f['return_departure_latest']))
    for key,symbol in [('attractions','attraction_names'),('restaurants','restaurant_names'),('attraction_types','spot_type'),('restaurant_types','food_type')]:
        if f['must_'+key]: codes.append(literal(f['must_'+key])+'<='+symbol)
        for name in f['excluded_'+key]: codes.append('not ('+literal([name])+'<='+symbol+')')
    q['hard_logic']=codes
    q['hard_logic_py']=template_programs(q)
    if any(c is None for c in q['hard_logic_py']): raise ValueError('存在尚未实现的硬约束模板')
    q['translation_mode']='structured_personalization_v1'
    q['translation_program_sources']=['template']*len(codes)
    return {'legacy_query':q,'request_fingerprint':fingerprint(request),'policy_version':'explicit_preferences_v1'}
