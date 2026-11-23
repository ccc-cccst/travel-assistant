"""Normalize public inputs and preserve evidence, residuals and conflicts."""
from copy import deepcopy
import hashlib
import json
import math
import re

from ._legacy_request import parse_request, PUBLIC_FIELDS
from .catalog import INTERESTS
from chinatravel.soft_constraints.scoring import clock

VERSION='trip_request_v1'
PROFILE_FIELDS={'interests','cuisines','hotel_features','required_hotel_features','pace','mobility',
                'focus','anchor','preferred_places','avoid_interests'}
FOCUS={'balanced','cost','lodging_cost','food_share','more_attractions','near_restaurants','anchor'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str,separators=(',',':')).encode()).hexdigest()


def normalize_request(value, env=None):
    if not isinstance(value,dict): raise ValueError('输入必须为对象')
    # Positive allowlist: reference fields are never copied, even into diagnostics.
    clean={k:deepcopy(v) for k,v in value.items() if k in PUBLIC_FIELDS and k!='profile'}
    if 'raw_text' in value: clean['nature_language']=value['raw_text']
    if 'trip' in value:
        for src,dst in [('origin_city','start_city'),('destination_city','target_city'),('days','days'),('people','people_number')]:
            if src in value['trip']: clean[dst]=value['trip'][src]
    parsed=parse_request(clean,env)
    profile=value.get('profile',{})
    if not isinstance(profile,dict): raise ValueError('profile必须为对象')
    if set(profile)-PROFILE_FIELDS: raise ValueError('未知偏好字段: '+str(sorted(set(profile)-PROFILE_FIELDS)))
    preferences=[]
    def add(dimension,target,source,priority='normal',direction='prefer'):
        if target is None: return
        if any(p['dimension']==dimension and p['target']==target and p['direction']==direction for p in preferences): return
        preferences.append({'id':'p'+str(len(preferences)+1),'dimension':dimension,'target':target,
                            'direction':direction,'priority':priority,'scope':'trip','source':source})
    for key,dimension in [('interests','interest'),('cuisines','cuisine'),('hotel_features','hotel_feature'),
                          ('preferred_places','place'),('avoid_interests','interest')]:
        values=profile.get(key,[])
        if not isinstance(values,list) or any(not isinstance(v,str) or not v.strip() for v in values): raise ValueError(key+'应为名称列表')
        for item in values:
            if dimension=='interest' and item not in INTERESTS: raise ValueError('未知兴趣 '+item)
            add(dimension,item,'profile.'+key,direction='avoid' if key=='avoid_interests' else 'prefer')
    for field,allowed in [('pace',{'relaxed','balanced','full'}),('mobility',{'less_walking','less_transit','public_transport'}),('focus',FOCUS)]:
        if field in profile:
            if profile[field] not in allowed: raise ValueError('无效'+field)
            if profile[field]!='balanced': add(field,profile[field],'profile.'+field,'high' if field=='focus' else 'normal')
    if profile.get('anchor'): add('anchor',profile['anchor'],'profile.anchor')
    text=parsed.nature_language
    for target,pattern in [('museum',r'博物馆|美术馆|艺术馆'),('history',r'历史人文|历史文化|历史古迹'),
                           ('nature',r'自然风光|自然景观'),('park',r'公园|园林')]:
        match=re.search(pattern,text)
        if match and re.search(r'喜欢|想|希望|偏好|看看|参观|多去',text[max(0,match.start()-12):match.end()]):
            # Negation is handled by the bounded parser; never add a positive wish for it.
            if not re.search(r'不想|不要|不去|避免',text[max(0,match.start()-5):match.start()]): add('interest',target,match[0])
    for target,pattern in [('relaxed',r'不要太累|不太累|轻松|舒缓|放松|不赶|不想太赶|不要特种兵'),('full',r'充实|紧凑|特种兵')]:
        match=re.search(pattern,text)
        if match and not (target=='full' and re.search(r'不要|不想|避免|不',text[max(0,match.start()-5):match.start()])):
            add('pace',target,match[0])
    for clause in re.split(r'[，。；;\n]',text):
        if re.search(r'少走路|少步行',clause): add('mobility','less_walking',clause)
        if not re.search(r'餐厅|餐馆',clause) and re.search(r'减少.*交通|少接驳|少通勤|少折返|(?:交通|移动).*时间.*(?:少|短)',clause):
            add('mobility','less_transit',clause)
    if re.search(r'(?:主要|优先).*(?:地铁|公交|公共交通)',text): add('mobility','public_transport',re.search(r'(?:主要|优先).*(?:地铁|公交|公共交通)',text)[0])
    for attr,dimension in [('preferred_attractions','place'),('preferred_restaurants','place'),('preferred_restaurant_types','cuisine')]:
        for target in getattr(parsed,attr): add(dimension,target,'text_catalog_match')
    for target in parsed.preferred_attraction_types:
        matches=[key for key,aliases in INTERESTS.items() if target in aliases]
        for tag in matches: add('interest',tag,'text_catalog_match')
    for target,pattern in [('more_attractions',r'尽可能多.*景点|景点.*尽可能多|参观多(?:个|些).*景点|参观很多景点|尽量多参观.*景点|参观更多.*景点'),('lodging_cost',r'住宿.*(?:尽可能少|尽可能低|便宜)|酒店.*(?:少花|便宜)|(?:减少|降低|最小化).*住宿'),
                           ('food_share',r'餐饮.*占比|饮食.*比例|餐饮.*比例|餐饮.*尽可能多'),('near_restaurants',r'餐(?:馆|厅).*(?:近|距离|交通时间)|(?:距离|交通).*餐(?:馆|厅)')]:
        if re.search(pattern,text): add('focus',target,re.search(pattern,text)[0],'high')
    for clause in re.split(r'[，。；;\n]',text):
        if re.search(r'附近|靠近',clause) and env is not None:
            city=parsed.target_city
            names=getattr(env.attractions,'data',{}).get(city)
            if names is not None:
                matches=[name for name in names['name'] if name in clause]
                for name in matches:
                    if not any(name!=other and name in other for other in matches): add('anchor',name,clause)
    issues=deepcopy(parsed.issues)
    directional_modes={}
    if re.search(r'(?:坐|乘)(?:火车|高铁|动车)去',text) and not re.search(r'往返|来回|只坐|只能|出行',text):
        directional_modes['outbound']=['train']; parsed.intercity_modes=('train','airplane')
    if re.search(r'(?:坐|乘)飞机去',text) and not re.search(r'往返|来回|只坐|只能|出行',text):
        directional_modes['outbound']=['airplane']; parsed.intercity_modes=('train','airplane')
    if parsed.assumptions and not clean.get('people_number'):
        issues.append({'code':'people_need_confirmation','source':'people_number','message':'请确认人数','blocking':True})
    for field in ['days','people_number']:
        if field in clean and (isinstance(clean[field],bool) or not str(clean[field]).isdigit() or int(clean[field])<1):
            issues.append({'code':'invalid_integer','source':field,'message':field+'须为正整数','blocking':True})
    limits={}
    for key in ['max_daily_access_walk_minutes','max_daily_transport_minutes','max_daily_attractions']:
        if key in value:
            n=value[key]
            if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or n<0: raise ValueError('无效限制 '+key)
            limits[key]=n
    if parsed.profile.get('hard_limits'):
        for key,n in parsed.profile['hard_limits'].items():
            if key=='max_daily_walk_minutes':
                issues.append({'code':'unknown_inside_walking','source':text,'message':'只有接驳步行数据，无法核验全日步行上限','blocking':True})
            else: limits[key]=n
    appointments=deepcopy(value.get('appointments',[]))
    if not isinstance(appointments,list): raise ValueError('appointments应为列表')
    for a in appointments:
        if not isinstance(a,dict) or not isinstance(a.get('name'),str) or type(a.get('day')) is not int or not 1<=a['day']<=parsed.days or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',a.get('start_time','')) or type(a.get('duration_minutes')) is not int or a['duration_minutes']<=0:
            raise ValueError('预约需要地点、有效日序、HH:MM和正整数时长')
        if env is not None and a['name'] not in set(env.attractions.data.get(parsed.target_city,{}).get('name',[])):
            issues.append({'code':'unknown_appointment_place','source':a['name'],'message':'预约地点未匹配到目的地景点','blocking':True})
    for i,a in enumerate(appointments):
        for b in appointments[i+1:]:
            if a['day']==b['day'] and max(clock(a['start_time']),clock(b['start_time']))<min(clock(a['start_time'])+a['duration_minutes'],clock(b['start_time'])+b['duration_minutes']):
                issues.append({'code':'overlapping_appointments','source':a['name'],'message':'预约时间相互重叠','blocking':True})
    for original in parsed.appointments:
        matched=next((a for a in appointments if a['name'] in original['place_options']
            and a['day']==original['day'] and a['start_time']==original['start_time']
            and a['duration_minutes']==clock(original['end_time'])-clock(original['start_time'])),None)
        if matched:
            issues=[i for i in issues if not (i['code']=='appointment_requires_confirmation' and i['source']==original['source'])]
    required=deepcopy(profile.get('required_hotel_features',[]))
    if not isinstance(required,list) or any(not isinstance(x,str) or not x for x in required): raise ValueError('无效酒店要求')
    unsupported=[{'source':i['source'],'reason':i['message'],'blocking':i['blocking']} for i in issues]
    if env is not None:
        from .catalog import PreferenceCatalog
        catalog=PreferenceCatalog(env,parsed.target_city)
        features=catalog.options()['hotel_features']
        for clause in re.split(r'[，。；;\n]',text):
            for feature in features:
                if len(feature)>1 and feature in clause and re.search(r'酒店|住宿|住的|宾馆',clause):
                    if re.search(r'必须|一定|需要',clause) and not re.search(r'最好|尽量',clause):
                        if feature not in required: required.append(feature)
                    elif not re.search(r'不要|不想|不能',clause): add('hotel_feature',feature,clause)
    for clause in parsed.soft_preferences:
        background=bool(re.search(r'(?:想去|计划去|准备去).*(?:玩|旅游|旅行)',clause)) and not re.search(r'景点|菜|酒店|餐|附近|喜欢|偏好|轻松|多|少',clause)
        if not background and not any(p['source'] in clause or clause in p['source'] for p in preferences) and not any(u['source']==clause for u in unsupported):
            unsupported.append({'source':clause,'reason':'已保留语义偏好，暂未找到可执行映射','blocking':False})
    for clause in re.split(r'[，。；;\n]',text):
        if re.search(r'养生|无障碍|过敏|免排队|咖啡',clause):
            unsupported.append({'source':clause,'reason':'此体验或安全条件尚不能完整核验','blocking':bool(re.search(r'必须|只能|过敏',clause))})
    facts={key:deepcopy(getattr(parsed,key)) for key in ('budget','hotel_price_limit','food_price_limit','rooms','room_type',
        'intercity_modes','local_modes','outbound_earliest','outbound_latest','return_departure_latest','return_arrival_latest',
        'must_attractions','must_restaurants','excluded_attractions','excluded_restaurants','must_attraction_types',
        'must_restaurant_types','excluded_attraction_types','excluded_restaurant_types')}
    for key in ('pace','interest'):
        positive={p['target'] for p in preferences if p['dimension']==key and p['direction']=='prefer'}
        if key=='pace' and len(positive)>1: issues.append({'code':'pace_conflict','source':'profile/text','message':'节奏选项与文字有冲突','blocking':True})
    for name in set(parsed.excluded_attractions)|set(parsed.excluded_restaurants):
        if any(p['dimension']=='place' and p['target']==name for p in preferences):
            issues.append({'code':'place_conflict','source':name,'message':'同时偏好和排除同一地点','blocking':True})
    return {'schema_version':VERSION,'request_id':parsed.uid or fingerprint(clean)[:16], 'request_revision':value.get('request_revision',1),
            'raw_text':text,'trip':{'origin_city':parsed.start_city,'destination_city':parsed.target_city,'days':parsed.days,
                                  'people':parsed.people_number,'start_date':value.get('start_date')},
            'facts':facts,'preferences':preferences,'locked_events':appointments,'hard_limits':limits,
            'required_hotel_features':required,'directional_modes':directional_modes,'issues':issues,'unmapped_requirements':unsupported,
            'evidence':parsed.evidence,'assumptions':parsed.assumptions,
            'ready':not any(i['blocking'] for i in issues+unsupported)}
