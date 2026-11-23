"""Optional grounded interpretation of additional soft wishes with the existing LLM."""
import json
import re
from copy import deepcopy


def interpret_preferences(request,catalog,model):
    options=catalog.options()
    allowed={'interest':{i['id'] for i in options['interests'] if i['count']},
             'cuisine':set(options['cuisines']),'hotel_feature':set(options['hotel_features']),
             'pace':{'relaxed','full'},'mobility':{'less_walking','less_transit','public_transport'},
             'focus':{'cost','lodging_cost','food_share','more_attractions','near_restaurants'}}
    prompt=('从用户原文抽取额外软偏好。只输出JSON {"items":[{"dimension":"interest","target":"museum","source":"原文完整分句"}]}。'
            'source必须逐字来自原文。不把否定变肯定。不把必须、只能、已预约等硬要求改成软偏好。'
            '保留原有事实，不猜测缺失数值或安全能力。target必须来自允许值。无法映射的条目不输出。\n'+
            json.dumps({'text':request['raw_text'],'existing':request['preferences'],
                        'allowed':{k:sorted(v) for k,v in allowed.items()}},ensure_ascii=False))
    response=model([{'role':'user','content':prompt}],one_line=False,json_mode=True,max_tokens=1500)
    value=json.loads(response); output=deepcopy(request); accepted=[]; rejected=0
    if not isinstance(value,dict) or not isinstance(value.get('items'),list): raise ValueError('模型偏好格式错误')
    for item in value['items']:
        if not isinstance(item,dict): rejected+=1;continue
        d,t,s=item.get('dimension'),item.get('target'),item.get('source')
        if (d not in allowed or t not in allowed[d] or not isinstance(s,str) or not s or s not in request['raw_text']
                or re.search(r'必须|只能|已预约|不想|不要|不能|不去|不吃',s)):
            rejected+=1;continue
        if not re.search(r'想|喜欢|希望|尽量|偏好|多|少|轻松|舒缓',s): rejected+=1;continue
        if any(p['dimension']==d and p['target']==t for p in output['preferences']):continue
        pref={'id':'p'+str(len(output['preferences'])+1),'dimension':d,'target':t,'source':s,
              'scope':'trip','direction':'prefer','priority':'normal'}
        output['preferences'].append(pref);accepted.append(pref)
    output['model_interpretation']={'accepted':len(accepted),'rejected':rejected,'source':'model_grounded_allowlist'}
    return output
