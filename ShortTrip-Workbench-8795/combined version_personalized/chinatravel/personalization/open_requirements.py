"""Interpret open-ended travel requests using their verified source spans."""

import re


NAMED_PLACES = {'hotel': 'accommodation', 'restaurant': 'restaurant', 'attraction': 'attraction'}
HARD_WORDS = re.compile(r'必须|一定要|非.{1,18}不可|务必|绝对|只能|不得|至少|最少|不少于|不超过|上限|以内|之内|限于|已.{0,5}(?:约|订)|不可更改|必去|排除|不要|不想|不能|不去')
PRIMARY = re.compile(r'主要(?:就是|是|就)?(?:为了|想|要|目的)|首要(?:目的|目标)|专程|特地|唯一目的')
OPTIONAL_VISIT = re.compile(r'(?:想(?:要)?|希望|喜欢|尽量|优先)(?:再|也|能|顺便|多|去|逛|看看|看|体验|参观|安排|打卡)+')
MORE_VISITS = re.compile(r'(?:多逛|多去|多看|多参观|多体验|多安排)')
MANDATORY_VISIT = re.compile(r'必须|一定要|务必|至少|最少|不少于|必去|已.{0,5}(?:约|订)')
EVENING_AREA = re.compile(r'(晚上|夜间|傍晚)(?:再|想|希望|可以|顺便)?(?:去|到)?'
                          r'(?:逛逛|逛一逛|走走|看看|逛|散步)?([\u4e00-\u9fff]{2,16}?)(?:一带|周边|附近)')


def _span(item, text):
    source = item['source']
    if source.get('type') != 'original_text':
        return None
    start, end = source.get('start'), source.get('end')
    if type(start) is int and type(end) is int and 0 <= start < end <= len(text):
        if text[start:end] == source.get('text'):
            return start, end
    return None


def normalize_open_experience(item, text):
    """Recover an optional category from a literal description, preserving explicit requirements."""
    field = item['field']
    if field not in ('experience_theme', 'experience_example', 'near_anchor', 'unmapped', *NAMED_PLACES):
        return item
    value = item['value']
    span = _span(item, text)
    if not span or not isinstance(value, str) or value not in text[slice(*span)]:
        return item
    start, end = span
    before = re.split(r'[，,。！？；;\n]', text[:start])[-1]
    clause = before + text[start:end] + re.split(r'[，,。！？；;\n]', text[end:], maxsplit=1)[0]
    if item['strength'] == 'hard':
        return item
    if field in ('experience_theme', 'experience_example', 'unmapped') and MANDATORY_VISIT.search(
        (before + text[start:end]).split(value, 1)[0]
    ):
        return {**item, 'field': 'unmapped', 'label': '待核实要求', 'strength': 'hard'}
    if HARD_WORDS.search(clause):
        if field == 'experience_theme' and item['strength'] == 'fact':
            return {**item, 'field': 'unmapped', 'label': '待核实要求', 'strength': 'hard'}
        return item
    area = EVENING_AREA.search(clause)
    if (area and re.search(r'逛|走走|散步|看看',clause) and field not in ('hotel', 'restaurant')
            and (area[2] in value or value in area[2])
            and item['operator'] not in ('avoid','exclude') and not re.search(r'第.{1,3}天|首日|末日|最后一天', clause)):
        return {**item, 'field': 'experience_theme', 'label': '傍晚游览区域' if area[1]=='傍晚' else '晚间游览区域',
                'strength': 'soft', 'operator': 'prefer', 'open_experience': True, 'area_term': area[2],
                'preferred_start': '17:00' if area[1]=='傍晚' else '18:00'}
    if field == 'near_anchor':
        return item
    primary = bool(PRIMARY.search(before + text[start:end]))
    if field in NAMED_PLACES:
        if primary and item['strength'] == 'soft' and item['operator'] in ('select','eq','include','prefer','prioritize'):
            return {**item, 'primary': True, 'operator': 'prioritize'}
        return item
    wants_more = bool(MORE_VISITS.search(before + text[start:end]))
    example = field == 'experience_example' and item['operator'] in ('eq', 'include', 'prefer')
    open_theme = field == 'experience_theme' and (
        wants_more or '想逛' in clause or '感兴趣' in text[start:end]
        or text[start:end].startswith(('并且去','还想去'))
    )
    if primary or open_theme or (
            example and OPTIONAL_VISIT.search(clause)):
        return {**item, 'field': 'experience_theme', 'label': '体验主题', 'strength': 'soft',
                'operator': 'prioritize' if primary else 'maximize' if wants_more else 'prefer',
                'open_experience': True, 'primary': primary, 'maximize_matches': wants_more}
    return item


def place_modifiers(items, text):
    """Bind nearby phrasing to one named place when the source proves that reading."""
    named = [(i, item, _span(item, text)) for i, item in enumerate(items, 1)
             if item['field'] in NAMED_PLACES and item['operator'] in ('eq', 'select', 'include', 'require', 'prefer','prioritize')
             and isinstance(item['value'], str) and item['value'].strip()]
    binding = {}
    for index, item in enumerate(items, 1):
        if (item['field'] != 'near_anchor' or item['strength'] not in ('fact', 'soft')
                or item['operator'] not in ('eq', 'near', 'prefer') or not isinstance(item['value'], str)):
            continue
        span = _span(item, text)
        if not span:
            continue
        start, end = span
        left = re.split(r'[，,。！？；;\n]', text[:start])[-1]
        right = re.split(r'[，,。！？；;\n]', text[end:], maxsplit=1)[0]
        clause = left + text[start:end] + right
        if HARD_WORDS.search(clause) or re.search(r'\d|[一二两三四五六七八九十百]+\s*(?:分钟|小时|公里|米)', clause):
            continue
        prefix = re.escape(item['value']) + r'\s*(?:附近|旁边|周边|旁|一带)\s*(?:的\s*)?'
        targets = []
        for target_index, target, target_span in named:
            if not target_span or not (target_span[0] < end + len(right) and target_span[1] > start - len(left)):
                continue
            target_name = target['value']
            if any(e.get('type') in ('user_confirmation', 'recovery_assumption')
                   for e in target.get('additional_sources', [])):
                branch_base = re.split(r'[（(]', target_name, 1)[0]
                if branch_base in text[slice(*target_span)]:
                    target_name = branch_base
            if re.search(prefix + re.escape(target_name), clause):
                targets.append(target_index)
        if len(targets) == 1:
            binding[f'requirement-{index}'] = f'requirement-{targets[0]}'
    return binding


def requirement_report(spec, request, unsupported, snapshot=None):
    """Expose the adapter's decision and grounding references for each original span."""
    bindings = place_modifiers(spec['items'], spec.get('raw_text', ''))
    records = {}
    for entry in (snapshot or {}).get('place_groundings', []):
        records.setdefault(entry['requirement_id'], []).append({
            'status': entry['status'], 'places': [p['name'] for p in entry['records']]})
    report = []
    for index, item in enumerate(spec['items'], 1):
        identifier = f'requirement-{index}'
        blocked = any(all(item[key] == other[key] for key in ('field', 'value', 'strength', 'operator', 'scope'))
                      for other in unsupported)
        target = bindings.get(identifier)
        treatment = ('needs_resolution' if blocked else 'place_modifier' if target else
                     'preference' if item['strength'] == 'soft' else 'required' if (
                         item['strength'] == 'hard' or item['field'] == 'attraction') else 'fact_or_context')
        report.append({'id': identifier, 'field': item['field'], 'value': item['value'],
                       'source': item['source'], 'strength': item['strength'], 'treatment': treatment,
                       'target': target, 'primary': item.get('primary', False),
                       'grounding': records.get(target or identifier, [])})
    return {'requirements': report, 'issues': spec['issues'],
            'hard_pins': {**request.get('required_hotel_ids', {}), **request.get('required_visit_ids', {})}}
