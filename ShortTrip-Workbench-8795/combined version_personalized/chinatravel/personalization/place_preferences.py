"""Apply an explicit destination map choice to a confirmed request revision."""

from .tripspec import label_tuple


MAP_CHOICES = {'want': ('prefer', 'soft', '想去景点'),
               'must': ('include', 'hard', '必去景点'),
               'skip': ('exclude', 'hard', '跳过景点')}
VISIT_OPERATORS = ('select', 'eq', 'require', 'include')


def update_place_preference(spec, facts, place_id, choice):
    if not isinstance(place_id, str) or not 0 < len(place_id) <= 160 or choice not in (*MAP_CHOICES, 'clear'):
        raise ValueError('请选择目录里的具体景点及本次安排。')
    city = next((item['value'] for item in spec['items'] if item['field'] == 'destination_city'), None)
    if not city:
        raise ValueError('请先确认本次旅行的目的地。')
    original = spec['items']
    items = [item for item in original if not (
        item['source'].get('type') == 'map_selection' and item['source'].get('place_id') == place_id)]
    if choice != 'clear':
        match = facts.place(city, place_id, 'attraction')
        if match['status'] != 'found':
            raise ValueError('当前目的地目录无法确认这个景点。')
        place, = match['records']
        if place['id'] != place_id or not place['planning_catalog'] or not place['routable_in_snapshot']:
            raise ValueError('该景点暂时不能用于可核验的行程。')
        for item in items:
            if item['field'] == 'appointment':
                other = item['value']['name']
                incompatible = choice == 'skip'
            elif item['field'] == 'attraction' and item['strength'] in ('hard', 'fact'):
                other = item['value']
                incompatible = (choice == 'skip' and item['operator'] in VISIT_OPERATORS
                                or choice != 'skip' and item['operator'] == 'exclude')
            else:
                continue
            if not incompatible or not isinstance(other, str) or not other.strip():
                continue
            grounded = facts.place(city, other, 'attraction')
            if place_id in {record['id'] for record in grounded['records']}:
                raise ValueError(f'「{place["name"]}」与已确认的预约或必去、跳过要求冲突，请先核对原要求。')
        operator, strength, label = MAP_CHOICES[choice]
        entry = label_tuple('attraction', operator, None, 'local_experience', strength, place['name'],
                            {'type': 'map_selection', 'place_id': place_id, 'choice': choice})
        entry['label'] = label
        if entry in original:
            return spec
        items.append(entry)
    if items == original:
        return spec
    return {**spec, 'items': items, 'request_revision': spec['request_revision'] + 1}
