"""Product planning scope, independent of the installed city catalog."""

FROZEN_CITIES = frozenset(('西安', '厦门'))


def planning_cities(catalog_cities):
    return [city for city in catalog_cities if city not in FROZEN_CITIES]


def frozen_city_outcome(request):
    trip = request['trip']
    affected = {field: trip[field] for field in ('origin_city', 'destination_city')
                if trip.get(field) in FROZEN_CITIES}
    if not affected:
        return None
    cities = '、'.join(dict.fromkeys(affected.values()))
    return {
        'state': 'planning_city_frozen',
        'reason_code': 'planning_city_frozen',
        'message': f'{cities}暂不开放行程规划，请选择当前开放的城市。',
        'evidence': {'frozen_city_fields': affected},
        'model_calls': 0,
    }


def prepare_for_planning(request):
    """Reflect the current scope on confirmation cards, including reopened requests."""
    original = request['issues']
    issues = [i for i in original if i['code'] != 'planning_city_frozen']
    outcome = frozen_city_outcome(request)
    if outcome:
        issues.append({'code': outcome['reason_code'], 'message': outcome['message'], 'blocking': True})
    elif len(issues) == len(original):
        return request
    ready = not any(item.get('blocking') for item in issues + request['unmapped_requirements'])
    return {**request, 'issues': issues, 'ready': ready}
