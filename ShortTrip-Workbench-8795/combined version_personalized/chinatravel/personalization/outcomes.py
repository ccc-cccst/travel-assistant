"""Explain observed failures without conflating travel cost and search time."""


def transport_precheck(request,env,observations=None):
    trip=request['trip'];origin,destination=trip['origin_city'],trip['destination_city']
    counts={};missing=[]
    for direction,source,target in [('outbound',origin,destination),('return',destination,origin)]:
        available={}
        for mode in ('train','airplane'):
            try:
                rows=env.intercitytransport.select(source,target,mode)
                available[mode]=None if isinstance(rows,str) else len(rows)
            except (ValueError,KeyError,TypeError,FileNotFoundError): available[mode]=None
        counts[direction]=available
        allowed=request.get('directional_modes',{}).get(direction,request['facts']['intercity_modes'])
        if allowed and all(available.get(mode)==0 for mode in allowed):missing.append(direction)
    if observations is not None:
        observations.update(transport_availability=counts,missing_directions=missing,
                            unknown_fields=[direction+'.'+mode for direction,values in counts.items() for mode,count in values.items() if count is None])
    if not missing:return None
    modes=request['facts']['intercity_modes']
    restriction='只坐飞机' if modes==['airplane'] or modes==('airplane',) else '只坐火车' if modes==['train'] or modes==('train',) else '当前交通要求'
    message=f'当前数据中未找到符合“{restriction}”条件的{origin}往返{destination}交通，无法开始行程搜索。'
    alternatives=[mode for mode in ('train','airplane') if all((counts[d].get(mode) or 0)>0 for d in ('outbound','return'))]
    if 'train' in alternatives and 'train' not in modes:message+='可以将城际交通改为“不限”或“只坐火车”后重试。'
    elif 'airplane' in alternatives and 'airplane' not in modes:message+='可以将城际交通改为“不限”或“只坐飞机”后重试。'
    else:message+='可以更换目的地或交通要求；这不代表现实中一定无法出行。'
    return {'state':'transport_unavailable','reason_code':'no_eligible_intercity_transport',
            'message':message,'transport_availability':counts,'missing_directions':missing,
            'scope':'local_snapshot_direct_city_pairs','model_calls':0,
            'note':'当前规划器尚未自动拼接飞往邻近城市再转车的城际路线。'}


def failure_outcome(run,worker_exit,candidate_count=0):
    reason=run.get('stop_reason')
    error=run.get('error') or {};kind=error.get('type') if isinstance(error,dict) else None
    if kind in ('AuthenticationError','PermissionDeniedError'):
        state,message='model_access_denied','模型服务拒绝了访问，请检查后端凭据与访问权限。'
    elif kind=='RateLimitError':
        state,message='model_rate_limited','模型服务返回限流或配额限制，请稍后重试或检查模型服务配额。'
    elif kind in ('APIConnectionError','APITimeoutError'):
        state,message='model_request_failed','模型请求连接失败或超时，本次规划未完成。请检查服务连接后重试。'
    elif worker_exit or run.get('error') or reason=='error':
        state,message='worker_failed','规划过程中出现运行错误，未能完成。请重试，持续失败时可查看任务记录。'
    elif candidate_count:
        state,message='verification_failed','已生成行程候选，但实际选中的方案未通过完整核验。请调整要求或重试。'
    elif reason in ('matched_wall_budget','wall_limit_reached'):
        state,message='search_timeout','规划时间已用尽，尚未找到通过核验的行程。可以重试或调整要求。'
    elif reason=='search_exhausted':
        state,message='search_exhausted','已尝试当前搜索范围，尚未找到通过核验的行程。可以调整交通、地点或其他要求后重试。'
    else:
        state,message='no_verified_result','本次没有得到通过核验的行程，具体原因尚未确定，请查看错误记录后重试。'
    evidence={'worker_error':run.get('error'),'worker_exit':worker_exit,'candidate_count':candidate_count}
    return {'state':state,'message':message,'stop_reason':reason,'reason_code':state,'evidence':evidence}
