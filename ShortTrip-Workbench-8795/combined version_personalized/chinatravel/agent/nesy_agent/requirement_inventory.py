"""Collect verbatim requirements before assigning DSL fields or hard/soft labels."""
import json
import re
from .translation_contract import TranslationValidationError, inventory_output_options


def collect_requirements(query, llm, lang='zh'):
    from chinatravel.environment.language import normalize_lang
    from .extraction_alignment import verbatim_source
    if normalize_lang(lang) == 'en':
        from .prompts.prompts_en import REQUIREMENT_EXTRACTION_INSTRUCTION as instruction
    else:
        from .prompts.prompts import REQUIREMENT_EXTRACTION_INSTRUCTION as instruction
    metadata = {key: query[key] for key in ('start_city', 'target_city', 'days', 'people_number') if key in query}
    prompt = instruction + '\nContext:\n' + json.dumps(metadata, ensure_ascii=False) + '\nRequest:\n' + query['nature_language']
    error = None
    for attempt in range(3):
        content = prompt + ('\nCorrect this source/format error:\n' + str(error) if error else '')
        response = llm([{'role': 'user', 'content': content}], one_line=False, json_mode=True,
                       **inventory_output_options(llm))
        try:
            parsed = json.loads(response)
            if not isinstance(parsed, dict) or set(parsed) != {'requirements'} or not isinstance(parsed['requirements'], list):
                raise TranslationValidationError('Return exactly the requirements array')
            result = []
            for item in parsed['requirements']:
                if not isinstance(item, dict) or set(item) != {'source', 'requirement'}:
                    raise TranslationValidationError('Each requirement needs source and requirement strings')
                if not all(isinstance(value, str) and value.strip() for value in item.values()):
                    raise TranslationValidationError('Requirement and source must be nonempty strings')
                item = dict(item, source=trim_metadata_edges(query, verbatim_source(query['nature_language'], item['source'])))
                if item not in result:
                    result.append(item)
            return [item for item in result if not metadata_only_source(query, item["source"])], attempt
        except (ValueError, TypeError, KeyError) as exc:
            error = exc
    raise TranslationValidationError('Requirement inventory failed after 3 attempts: ' + str(error))


def inventory_coverage(inventory, result):
    """Use distinct evidence slots so one broad quote cannot hide several needs."""
    evidence = result.get('hard_logic_evidence', []) + result.get('soft_logic_evidence', [])
    sources = [item['source'] for item in evidence if item.get('source') != 'input metadata / passenger-count derivation']
    unmapped = result.get('unmapped_requirements', [])
    pending = [item for item in inventory if not any(item['source'] in source for source in unmapped)]
    options = [[index for index, source in enumerate(sources) if item['source'] in source] for item in pending]
    assigned = {}

    def match(requirement, visited):
        for evidence_index in options[requirement]:
            if evidence_index in visited:
                continue
            visited.add(evidence_index)
            if evidence_index not in assigned or match(assigned[evidence_index], visited):
                assigned[evidence_index] = requirement
                return True
        return False

    return [item for index, item in enumerate(pending) if not match(index, set())]


def metadata_only_source(query, source):
    """Ignore only clauses composed entirely of existing basic context wording."""
    if not (re.fullmatch(r'我(?:们|自己|和(?:女|男)?朋友)?', source.strip())
            or any(query.get(field) and query[field].casefold() in source.casefold() for field in ('start_city', 'target_city'))
            or re.search(r'(?:[0-9一二两三四五六七八九十]+\s*(?:个?(?:人|朋友)|天|位朋友|名朋友|口人)|\b(?:one|two|three|four|five|\d+)\s+(?:people|persons?|days?)\b)', source, re.I)):
        return False
    if not metadata_counts_match(query, source):
        return False
    remaining = source
    for field in ('start_city', 'target_city'):
        city = query.get(field)
        if isinstance(city, str) and city:
            remaining = re.sub(re.escape(city), '', remaining, flags=re.I)
    # A strict residual check keeps mixed clauses, age, transport and preferences.
    words = (
        '当前位置', '坐标', '出发城市', '目的地', '出行人数', '旅行人数', '行程天数', '旅行天数',
        '女朋友', '男朋友', '朋友', '我们', '打算', '计划', '准备', '前往', '想要',
        '游玩', '旅游', '旅行', '行程', '时长', '出行', '同行', '出发', '个人', '目前在', '现在在', '当前在', '独自', '一起', '我的', '家人', '家属', '一家', '口', '共', '跟',
        '我', '和', '与', '从', '到', '去', '来', '玩', '想', '人', '个', '名', '位', '天', '为', '是',
    )
    remaining = re.sub('|'.join(map(re.escape, words)), '', remaining)
    remaining = re.sub(r'\b(?:current(?:ly)?|location|origin|destination|city|we|i|my|and|with|'
                       r'people|persons?|travel(?:ing)?|trip|plan(?:ning)?|to|from|visit|stay|for|days?|'
                       r'want|would|like|one|two|three|four|five|six|seven|eight|nine|ten)\b', '', remaining, flags=re.I)
    remaining = re.sub(r'[0-9一二两三四五六七八九十百零〇\s，,。.;；、:：!！?？()（）]+', '', remaining)
    return not remaining and bool(source.strip())


def restore_unmapped_sources(query, values):
    """Rebind an exact, unique inventory description to its original source."""
    if not isinstance(values, list):
        raise TranslationValidationError('unmapped_requirements must contain literal original clauses')
    from .extraction_alignment import verbatim_source
    restored = []
    for value in values:
        try:
            source = verbatim_source(query['nature_language'], value)
        except TranslationValidationError:
            matches = {item['source'] for item in query.get('requirement_inventory', [])
                       if item['requirement'] == value}
            if len(matches) != 1:
                raise TranslationValidationError('unmapped_requirements must contain literal original clauses')
            source = matches.pop()
        if source not in restored:
            restored.append(source)
    # Companion demographics have no standalone DSL field. Retain their exact
    # clause as context when removing only demographic words leaves basic metadata.
    for item in query.get('requirement_inventory', []):
        source = item['source']
        if not re.search(r'孩子|儿童|小孩|宝宝|老人|父母|爸妈', source):
            continue
        basic = re.sub(r'带着|带|孩子|儿童|小孩|宝宝|老人|父母|爸妈', '', source)
        if metadata_only_source(query, basic) and source not in restored:
            restored.append(source)
    return restored


def metadata_counts_match(query, source):
    from .constraint_compiler import _number_present
    pattern = (r'(\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万]+|one|two|three|four|five|six|seven|eight|nine|ten)'
               r'\s*(天|个朋友|位朋友|名朋友|朋友|个人|人(?!民币)|days?\b|people\b|persons?\b)')
    for token, unit in re.findall(pattern, source, re.I):
        field = 'days' if unit == '天' or unit.lower().startswith('day') else 'people_number'
        expected = query.get(field)
        if '朋友' in unit and re.search(r'我[^，,。;；]{0,8}(?:和|与|跟)', source):
            expected = expected - 1 if expected is not None else None
        if expected is None or not _number_present(expected, token):
            return False
    return True


def trim_metadata_edges(query, source):
    """Trim only whole basic-context clauses at the edges of a quoted span."""
    pieces = list(re.finditer(r'[^，,。;；\n]+', source))
    while pieces and metadata_only_source(query, pieces[0][0]):
        pieces.pop(0)
    while pieces and metadata_only_source(query, pieces[-1][0]):
        pieces.pop()
    if not pieces:
        return source
    return source[pieces[0].start():pieces[-1].end()]
