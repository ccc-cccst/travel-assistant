"""Source coverage and bounded, item-wise extraction repair; no search logic."""
import ast
import difflib
import json
import re
from copy import deepcopy
from functools import lru_cache
from .translation_contract import TranslationValidationError,extraction_output_options
from .constraint_compiler import comparison, _catalog, _validate_extraction_items, compile_constraint, constraint_atoms, SET_FIELDS, NUMERIC_FIELDS, TIME_FIELDS, NUMERIC_OPERATORS, numeric_source_clause, chinese_number
from .semantic_grounding import TAG_ALIASES, LOCAL_CUISINE, category_values, grounding_instructions, local_cuisine_values
from .requirement_inventory import collect_requirements, inventory_coverage, restore_unmapped_sources

TRANSLATION_FLOW_VERSION = 6


def translation_cache_matches(query, cached):
    if not isinstance(cached, dict) or cached.get('translation_flow_version') != TRANSLATION_FLOW_VERSION:
        return False
    if not isinstance(cached.get('requirement_inventory'), list):
        return False
    fields = ('uid', 'start_city', 'target_city', 'days', 'people_number', 'nature_language')
    return all(query.get(field) == cached.get(field) for field in fields)


CATEGORY_ALIASES = TAG_ALIASES['spot_type']

def literal(values):return '{'+', '.join(repr(v) for v in sorted(values))+'}'
def set_code(field,values,any_of=False):
    if any_of and len(values)>1:
        return '('+' or '.join(literal({v})+'<='+field for v in sorted(values))+')'
    return literal(values)+'<='+field

@lru_cache(maxsize=32768)
def _literal_pattern(value):
    return re.compile(re.escape(value))


def aliases(names):
    # Return fresh containers so callers cannot mutate the shared cached index.
    return {alias: set(options) for alias, options in _alias_entries(frozenset(names))}


@lru_cache(maxsize=64)
def _alias_entries(names):
    result={}
    for name in sorted(names):
        result.setdefault(name,set()).add(name)
        for suffix in ('风景名胜区','风景区','景区','游船'):
            if name.endswith(suffix) and len(name)>len(suffix)+1:
                if suffix=='游船' and not name[:-len(suffix)].endswith('号'):continue
                result.setdefault(name[:-len(suffix)],set()).add(name)
        match=re.fullmatch(r'(.+?)[(（][^()（）]*校区[)）]',name)
        if match:result.setdefault(match[1],set()).add(name)
        branch = re.fullmatch(r'(.+?)[(（][^()（）]+[)）]', name)
        if branch and len(branch[1]) >= 4:
            result.setdefault(branch[1], set()).add(name)
    for alias, canonical in {'故宫': '故宫博物院', '上海迪士尼': '上海迪士尼度假区', '迪士尼': '上海迪士尼度假区', '金牛湖野生动物园': '金牛湖野生动物王国', '米桃-百花山房·桃花': '米桃-百花山房·桃花扇(金鹰世界店)'}.items():
        if canonical in names:
            result.setdefault(alias, set()).add(canonical)
    from chinatravel.environment.language import CITY_NAMES
    for name in sorted(names):
        for cities in CITY_NAMES.values():
            for city in cities:
                if not name.startswith(city):
                    continue
                short = name[len(city):].strip()
                if len(short) >= 2 and short not in CATEGORY_ALIASES and len([candidate for candidate in names if short in candidate]) == 1:
                    result.setdefault(short, set()).add(name)
    return tuple((alias, frozenset(options)) for alias, options in result.items())

def resolve_name(value,field,catalog):
    mapping=aliases(catalog[field]);options=mapping.get(value)
    if options is None:
        normalized=re.sub(r'\s+','',value).casefold()
        options={name for alias,names in mapping.items() if re.sub(r'\s+','',alias).casefold()==normalized for name in names}
    if options:
        if len(options)==1:return set(options),False
        # Multiple campuses of an explicitly named university are alternatives.
        if all(re.fullmatch(re.escape(value)+r'[(（][^()（）]+[)）]',n) for n in options):
            return set(options),True
    candidates=sorted(options or {n for n in catalog[field] if value in n})
    if not candidates:candidates=difflib.get_close_matches(value,sorted(catalog[field]),n=5,cutoff=.35)
    raise TranslationValidationError(f'Ambiguous or unresolved {field} {value!r}; canonical candidates: {candidates[:5]}; do not select arbitrarily')

def positive(text,start,modality='hard',field=None):
    return source_modality(text,text[start:start+1],start,field=field)[0]==modality


SOFT_WORDS = re.compile(
    r'最好|尽量|尽可能|优先|主要|为主|如果有时间|如果方便|喜欢|希望|都可以|均可|都行|'
    r'想(?:要)?|可以(?:去|吃|住|坐|看|参观|体验|游览|逛)|'
    r'\bprefer(?:red|ably|ence)?\b|\bmainly\b|\bprimarily\b|as much as possible|if possible|'
    r'\bwould like\b|\bwant(?:s)?(?: to)?\b|\bhope(?: to)?\b|\bcan (?:visit|eat|try|stay|see|take)\b', re.I)
STRONG_SOFT_WORDS = re.compile(r'最好|尽量|尽可能|优先|主要|为主|如果|prefer|mainly|primarily|if possible', re.I)
NEGATIVE_WORDS = re.compile(r'不可以|不能|不允许|禁止|避免|不打车|不吃|不喝|不住|别去|别吃|不去|不坐|不用|不乘|不要|不想|无需|不需要|\bavoid\b|\bdo not\b|\bdon.t want\b|\bmust not\b|\bcannot\b', re.I)
HARD_WORDS = re.compile(r'必须|只能|务必|一定要|\bmust\b|\brequire(?:d)?\b|\bonly\b', re.I)
NUMERIC_LIMIT = re.compile(r'预算[^，,。;；\n]{0,12}[0-9一二两三四五六七八九十百千万]|'
                           r'(?:不超过|不要超过|不得超过|少于|低于|小于|至少|不少于)[^，,。;；\n]*[0-9一二两三四五六七八九十百千万]|'
                           r'\b(?:budget|at most|at least|no more than|under)\b[^,;.\n]*\d', re.I)


def verbatim_source(text, source):
    if not isinstance(source,str) or not source.strip():
        raise TranslationValidationError('Evidence must be a verbatim source quote')
    if source in text:
        return source
    pattern=r'\s*'.join(re.escape(part) for part in source.split())
    match=re.search(pattern,text,re.I)
    if match is None:
        raise TranslationValidationError('Evidence must be a verbatim source quote; only whitespace/case formatting can be normalized')
    return match[0]


SCOPE_SEPARATOR = re.compile(
    r'[，,。;；\n]|\.(?=\s|$)|'
    r'(?:但是|但|并且|而且|同时)\s*(?=(?:我(?:们)?\s*)?(?:必须|只能|想|希望|可以|最好|尽量|不|要))|'
    r'\b(?:and|but)\s+(?=(?:(?:I|we|they)\s+)?(?:must|would|want|prefer|can|should)\b)', re.I)
ROOM_REQUIREMENT = re.compile(r'(?:[0-9一二两三四五六七八九十]+\s*间|大床房|单床房|双床房|\b(?:\d+|one|two|three)\s+rooms?\b|\btwin\b|\b(?:king|queen|double|single)[ -]bed\b)', re.I)


def source_context(text, source, start=None):
    """Recover the clause so a short evidence quote cannot hide a modifier."""
    if not isinstance(source,str) or not source or source not in text:
        raise TranslationValidationError('Evidence must be a verbatim source quote')
    start=text.index(source) if start is None else start
    before=SCOPE_SEPARATOR.split(text[:start])[-1]
    after=SCOPE_SEPARATOR.split(text[start+len(source):])[0]
    return before+source+after


def source_modality(text, source, start=None, field=None):
    if not isinstance(source, str) or not source or source not in text:
        raise TranslationValidationError('Evidence must be a verbatim source quote')
    positions = [start] if start is not None else [match.start() for match in re.finditer(re.escape(source), text)]
    results = []
    for position in positions:
        context = source_context(text, source, position)
        classification_context = numeric_source_clause(field, context) if field in ('cost', 'hotel_price', 'food_price') else context
        explicit = bool(HARD_WORDS.search(classification_context))
        cleaned = NEGATIVE_WORDS.sub('', classification_context)
        strong_soft = bool(STRONG_SOFT_WORDS.search(cleaned))
        numeric_field = field is None or field in NUMERIC_FIELDS | TIME_FIELDS
        has_amount = bool(re.search(r'[0-9一二两三四五六七八九十百千万]', classification_context))
        has_operator = any(re.search(pattern, classification_context, re.I) for operator, pattern in NUMERIC_OPERATORS)
        numeric = numeric_field and (bool(NUMERIC_LIMIT.search(classification_context)) or (has_amount and has_operator))
        room = field in (None, 'rooms', 'room_type') and bool(ROOM_REQUIREMENT.search(context))
        bound = numeric or room
        soft = bool(SOFT_WORDS.search(cleaned)) and not explicit
        if bound and not strong_soft:
            soft = False
        negative = bool(NEGATIVE_WORDS.search(classification_context)) and not numeric
        if negative:
            modality = 'negative_soft' if soft else 'negative'
        else:
            modality = 'soft' if soft else 'hard'
        results.append((modality, context))
    return next((result for result in results if result[0] == 'hard'), results[0])


def room_number(value):
    if value.isdigit():
        return int(value)
    digits={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if '十' in value:
        tens,ones=value.split('十',1)
        return digits.get(tens,1)*10+digits.get(ones,0)
    return digits.get(value)


def combine_alternatives(entries, text):
    groups = {}
    for entry in entries:
        if entry['field'] not in SET_FIELDS or not entry['code']:
            continue
        context = source_context(text, entry['source'])
        if re.search(r'或者|或|\bor\b', context, re.I):
            groups.setdefault((entry['field'], context), []).append(entry)
    for (field, context), choices in groups.items():
        if len(choices) < 2:
            continue
        values = {value for choice in choices for value in choice['values']}
        entries = [entry for entry in entries if entry not in choices]
        entries.append(dict(code=set_code(field, values, True), source=context,
                            field=field, values=sorted(values), any_of=True))
    return entries



def location_anchor(text, start, end, field, named_spans=()):
    """Identify a landmark locating a different requested accommodation or POI."""
    context = source_context(text, text[start:end], start)
    previous = SCOPE_SEPARATOR.split(text[:start])
    target_context = context + (previous[-2] if len(previous) > 1 else '')
    target_role = re.search(r'酒店|住宿|民宿|住|餐馆|餐厅|茶社|茶馆|咖啡店|吃|喝|hotel|stay|restaurant', target_context, re.I)
    after_name = text[end:]
    scope_after = SCOPE_SEPARATOR.split(after_name)[0]
    target_follows = any(other_start >= end and other_start < end + len(scope_after)
                         for other_start, other_end in named_spans)
    if re.match(r'\s*(?:附近|旁边|周边)(?:的)?', after_name) and (target_role or target_follows):
        return True
    return bool(field in ('attraction_names', 'spot_type')
                and re.search(r'附近|旁边|周边|near|close to', context, re.I)
                and target_role
                and not re.search(r'去|参观|游览|游玩|玩|逛|visit|tour', context, re.I))


def preference_coverage(query,catalog):
    return coverage(query,catalog,modality='soft')

def coverage(query,catalog,modality='hard'):
    text=query['nature_language'];entries=[];occupied=[]
    named_spans = [(match.start(), match.end())
                   for name_field in ('attraction_names', 'restaurant_names', 'hotel_names')
                   for alias in aliases(catalog[name_field]) if len(alias) >= 2
                   for match in _literal_pattern(alias).finditer(text)]
    def add(code,source,field,values=None,any_of=False):
        if not any(e['code']==code for e in entries):
            entries.append(dict(code=code,source=source,field=field,values=sorted(values) if values else None,any_of=any_of))
    for field in ('attraction_names','restaurant_names','hotel_names'):
        mapping=aliases(catalog[field]);mentions=[]
        for alias,options in mapping.items():
            if len(alias)<2:continue
            for match in _literal_pattern(alias).finditer(text):
                if alias not in catalog[field] and re.match(r'[\u4e00-\u9fff]', text[match.end():match.end()+1]):
                    following = text[match.end():]
                    if not following.startswith(('玩', '看', '参观', '游览', '游玩', '附近', '旁边', '周边', '和', '或', '及', '的', '必须', '一定', '务必', '想', '要', '去', '不')):
                        continue
                if location_anchor(text, match.start(), match.end(), field, named_spans):
                    occupied.append(match.span())
                    continue
                if any(start <= match.start() and end >= match.end() and end - start > len(alias) for start, end in named_spans):
                    continue
                if positive(text,match.start(),modality,field=field):mentions.append((match.start(),match.end(),alias))
        selected=[]
        for start,end,alias in sorted(mentions,key=lambda x:-(x[1]-x[0])):
            if any(start<e and end>s for s,e,_ in selected):continue
            selected.append((start,end,alias))
        for start,end,alias in sorted(selected):
            try:values,any_of=resolve_name(alias,field,catalog)
            except TranslationValidationError as exc:
                entries.append(dict(code=None,source=alias,field=field,values=None,any_of=False,pending=str(exc)))
                occupied.append((start,end));continue
            add(set_code(field,values,any_of),alias,field,values,any_of);occupied.append((start,end))
    masked=list(text)
    for start,end in occupied:masked[start:end]=' '* (end-start)
    remaining=''.join(masked)
    used=[]
    for alias,canonical in sorted(CATEGORY_ALIASES.items(),key=lambda x:-len(x[0])):
        for match in re.finditer(re.escape(alias),remaining):
            if location_anchor(text, match.start(), match.end(), 'spot_type', named_spans):
                used.append(match.span())
                continue
            if any(match.start()<e and match.end()>s for s,e in used):continue
            if positive(text,match.start(),modality,field='spot_type') and canonical in catalog['spot_type']:
                add(set_code('spot_type',{canonical}),alias,'spot_type',{canonical});used.append(match.span())
    for cuisine in sorted(catalog['food_type'],key=lambda v:-len(v)):
        if len(cuisine)>1:
            match=re.search(re.escape(cuisine),remaining)
            if match and positive(text,match.start(),modality,field='food_type'):add(set_code('food_type',{cuisine}),cuisine,'food_type',{cuisine})
    for match in LOCAL_CUISINE.finditer(remaining):
        if positive(text, match.start(), modality, field='food_type'):
            local_source = source_context(text, match[0], match.start())
            values = local_cuisine_values(query, catalog, local_source)
            if values:
                add(set_code('food_type', values, len(values) > 1), local_source, 'food_type', values, len(values) > 1)
    feature_spans = []
    for feature in sorted(catalog['hotel_feature'],key=lambda value:-len(value)):
        for match in re.finditer(re.escape(feature),remaining,re.I):
            if any(match.start() < end and match.end() > start for start, end in feature_spans):
                continue
            feature_spans.append(match.span())
            if positive(text,match.start(),modality,field='hotel_feature'):
                add(set_code('hotel_feature',{feature}),match[0],'hotel_feature',{feature})
    budget=re.search(r'(?:预算(?:在|为|是|约|大概|大约)?|budget\s*(?:of|is)?\s*)\s*(\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万]+)\s*([万千]?)(?![0-9零一二两三四五六七八九十百千万])(?!\s*(?:个|人(?!民币)|位|名|间|天|晚|床|房|顿|餐))',text,re.I)
    budget_scope = source_context(text, budget[0], budget.start()) if budget else ''
    local_price_scope = bool(re.search(r'酒店|住宿|房费|每晚|餐|饭|每顿|hotel|accommodation|per night|meal|dining', budget_scope, re.I))
    if budget and not local_price_scope and positive(text,budget.start(),modality,field='cost'):
        number = float(budget[1]) if re.fullmatch(r'\d+(?:\.\d+)?', budget[1]) else float(chinese_number(budget[1]))
        amount=number*{'':1,'万':10000,'千':1000}[budget[2]]
        if amount.is_integer():amount=int(amount)
        add('cost<='+repr(amount),budget[0],'cost')
    for match in re.finditer(r'(\d+|[一二两三四五六七八九十]+)\s*间(?=\s*(?:单床房|双床房|大床房|客房|房间|房|单人房|双人间|酒店|有[^，。;；\n]{0,20}酒店))',text):
        if positive(text,match.start(),modality,field='rooms'):
            number=room_number(match[1])
            if number:
                prefix=text[max(0,match.start()-5):match.start()]
                operator='>=' if re.search(r'至少|不少于|最少',prefix) else '<=' if re.search(r'至多|不超过|最多',prefix) else '=='
                add('rooms'+operator+str(number),match[0],'rooms')
    for match in re.finditer(r'\b(one|two|three|four|five|\d+)\s+(?:(?:single|twin|double|king|queen)[ -]?(?:bed)?\s+)?rooms?\b',text,re.I):
        if positive(text,match.start(),modality,field='rooms'):
            number=int(match[1]) if match[1].isdigit() else {'one':1,'two':2,'three':3,'four':4,'five':5}[match[1].lower()]
            add('rooms=='+str(number),match[0],'rooms')
    for pattern,beds in [(r'单床|大床|\b(?:single|double|king|queen)[ -]bed(?:ded)?\b',1),
                         (r'双床|\btwin(?:[ -]bed(?:ded)?)?\b|\btwo beds\b',2)]:
        for match in re.finditer(pattern,text,re.I):
            if positive(text,match.start(),modality,field='room_type'):
                add('room_type=='+str(beds),match[0],'room_type')
    for pattern,field,mode in [(r'火车|高铁|动车|\btrain\b','intercity_transport','train'),
                               (r'飞机|航班|\bflight\b','intercity_transport','airplane'),
                               (r'地铁|\bmetro\b|\bsubway\b','transport_type','metro')]:
        for match in re.finditer(pattern,text,re.I):
            if positive(text,match.start(),modality,field=field):
                operator='==' if field=='intercity_transport' else '<='
                add(field+operator+literal({mode}),match[0],field,{mode})
    prefix=re.search(r'高铁\s*[(（]?\s*(G)(?![A-Za-z])|(?<![A-Za-z])(G)\s*(?:字头|字首)',text,re.I)
    if prefix and positive(text,prefix.start(),modality,field='train_type'):add("train_type=={'G'}",prefix[0],'train_type',{'G'})
    deadline=re.search(r'(?:不超过|不晚于|最晚|\bby\s+|\bbefore\s+)\s*(\d{1,2})(?:[:：](\d{2})|点(?:钟)?)',text,re.I)
    if deadline and positive(text,deadline.start(),modality,field='return_start_time') and re.search(r'返|回来|上火车|上车|return',text,re.I):
        hour=int(deadline[1]);minute=int(deadline[2] or 0)
        if hour>23 or minute>59:raise TranslationValidationError('Invalid explicit return deadline')
        add("return_start_time<="+repr(f'{hour:02d}:{minute:02d}'),deadline[0],'return_start_time')
    for field in ('rooms','room_type'):
        equalities=[e['code'] for e in entries if e['field']==field and comparison(e['code']) and isinstance(comparison(e['code'])[1],ast.Eq)]
        if len(set(equalities))>1:
            raise TranslationValidationError('Multiple room configurations cannot be represented by a single global '+field+' constraint; preserve the original request for clarification')
    return combine_alternatives(entries, text)

def facts_in(code):
    fact=comparison(code)
    if fact:return [fact]
    try:tree=ast.parse(code,mode='eval').body
    except SyntaxError:return []
    if isinstance(tree,ast.BoolOp) and isinstance(tree.op,ast.Or):
        facts=[comparison(ast.unparse(node)) for node in tree.values]
        if all(facts):return facts
    return []

def ast_key(code):
    try:return ast.dump(ast.parse(code,mode='eval'))
    except (SyntaxError,TypeError):return None

def derived_fields(query):
    people = query.get('people_number')
    values = dict(days=query.get('days'), people_number=people, tickets=people,
                  taxi_cars=(people + 3) // 4 if people else None)
    return {field: value for field, value in values.items() if value is not None}


def fallback_translation(query, extracted, error):
    """Allow search with grounded template constraints when full translation fails."""
    evidence = [dict(constraint=f'{field}=={value}',
                     source='input metadata / passenger-count derivation', derived=True)
                for field, value in derived_fields(query).items()]
    accepted_extraction = extracted.get('translation_flow_version') == TRANSLATION_FLOW_VERSION
    original = query['nature_language']
    if accepted_extraction:
        evidence.extend(extracted['hard_logic_evidence'])
    elif not re.search(r'如果|假如|倘若|要是|除非|否则|或|\b(?:if|unless|or)\b', original, re.I):
        # The local checklist only recognizes positive, explicit requirements.
        # Leave alternatives and conditions to the full extractor.
        try:
            entries = coverage(query, _catalog(query['target_city']))
        except TranslationValidationError:
            entries = []
        for entry in entries:
            if not entry['code']:
                continue
            item = dict(constraint=entry['code'], source=entry['source'])
            try:
                _validate_extraction_items(query, dict(hard_logic=[entry['code']], hard_logic_evidence=[item]))
            except TranslationValidationError:
                continue
            evidence.append(item)

    programs, applied = {}, {}
    for item in evidence:
        code = item['constraint']
        program = compile_constraint(code)
        if program is not None:
            programs[code] = program
            applied[code] = item
    return dict(query, hard_logic=list(programs), hard_logic_evidence=list(applied.values()),
                hard_logic_py=list(programs.values()), translation_program_sources=['template'] * len(programs),
                soft_logic=extracted['soft_logic'] if accepted_extraction else [],
                soft_logic_evidence=extracted['soft_logic_evidence'] if accepted_extraction else [],
                unmapped_requirements=[original], translation_fallback={
                    'status': 'needs_review', 'reason': str(error), 'original_request': original,
                    'applied_constraints': list(programs),
                })


class ExtractionState:
    def __init__(self,query):
        self.query=deepcopy(query);self.catalog=_catalog(query['target_city'])
        self.checklist=coverage(query,self.catalog);self.locked={};self.errors=[]
        self.preference_checklist=preference_coverage(query,self.catalog)
        self.preferences={}
        self.derived=derived_fields(query)
        for field,value in self.derived.items():
            code=field+'=='+str(value)
            self.locked[code]=dict(constraint=code,source='input metadata / passenger-count derivation',derived=True)

    def instructions(self):
        return ('\nSource coverage checklist (all explicit items must remain; alternatives use OR):\n'
                +json.dumps(self.checklist,ensure_ascii=False)
                +'\nAlready validated constraints (locked; do not remove or alter):\n'
                +json.dumps(list(self.locked),ensure_ascii=False)
                +'\nEvidence for those validated constraints:\n'
                +json.dumps(list(self.locked.values()),ensure_ascii=False)
                +'\nMandatory user-semantic requirements to preserve in soft_logic with required satisfaction (never omit as optional ranking hints):\n'
                +json.dumps(self.preference_checklist,ensure_ascii=False)
                +'\nReturn hard_logic and soft_logic as arrays of STRINGS, never arrays of evidence objects. Put constraint/source objects only in hard_logic_evidence or soft_logic_evidence. Return complete arrays for every requirement after each repair. Every soft_logic request must remain an explicit satisfaction requirement. Requested food tags require at least one matching meal across the trip, unless the source explicitly requires every meal. No invented requirements.\n')

    def normalize(self, code, source):
        constraint_atoms(code)
        tree = ast.parse(code, mode='eval').body
        if isinstance(tree, ast.UnaryOp):
            parts = self.normalize(ast.unparse(tree.operand), source)
            if len(parts) != 1:
                raise TranslationValidationError('Keep each exclusion separate')
            return [('not (' + parts[0][0] + ')', source)]
        if isinstance(tree, ast.BoolOp):
            operation = ' or ' if isinstance(tree.op, ast.Or) else ' and '
            branches = []
            for node in tree.values:
                parts = self.normalize(ast.unparse(node), source)
                branches.append(parts[0][0] if len(parts) == 1 else '(' + ' and '.join(part[0] for part in parts) + ')')
            return [('(' + operation.join(branches) + ')', source)]
        field, op, value, shape = comparison(code)
        if field in self.derived:
            if not isinstance(op, ast.Eq) or value != self.derived[field]:
                raise TranslationValidationError(f'{field} must match authoritative input {self.derived[field]}')
            return [(field + '==' + str(value), None)]
        if shape == 'set' and field in SET_FIELDS and isinstance(value, set):
            result = []
            for item in sorted(value):
                if field.endswith('_names'):
                    values, any_of = resolve_name(item, field, self.catalog)
                else:
                    values = category_values(self.query, self.catalog, field, item, source=source)
                    if not values:
                        raise TranslationValidationError('No grounded destination tag for ' + item)
                    any_of = len(values) > 1
                canonical = set_code(field, values, any_of)
                entry = next((entry for entry in self.checklist + self.preference_checklist if entry['code'] == canonical), None)
                result.append((canonical, source if source is not None else entry['source'] if entry else None))
            return result
        return [(code, source)]

    def covered(self,entry,codes=None):
        codes = self.locked if codes is None else codes
        if entry['code'] is None:return False
        if entry['any_of']:
            return any(len(facts_in(c))>1 and all(f[0]==entry['field'] for f in facts_in(c))
                       and set(entry['values'])<={v for f in facts_in(c) for v in f[2]} for c in codes)
        expected=comparison(entry['code'])
        for code in codes:
            actual=comparison(code)
            if not actual:continue
            if expected[0]!=actual[0] or type(expected[1])!=type(actual[1]) or expected[3]!=actual[3]:continue
            if isinstance(expected[2],set) and isinstance(actual[2],set):
                if expected[2]<=actual[2]:return True
            elif expected[2]==actual[2]:return True
        return False

    def accept(self, parsed):
        if not isinstance(parsed, dict):
            raise TranslationValidationError('Expected extraction JSON object')
        errors = []
        candidate_hard = dict(self.locked)
        candidate_soft = dict(self.preferences)
        unmapped = restore_unmapped_sources(self.query, parsed.get('unmapped_requirements', []))
        for kind in ('hard', 'soft'):
            codes = parsed.get(kind + '_logic', [])
            evidence = parsed.get(kind + '_logic_evidence', [])
            if not isinstance(codes, list) or not all(isinstance(code, str) and code.strip() for code in codes):
                raise TranslationValidationError(kind + '_logic must contain nonempty strings')
            if not isinstance(evidence, list) or not all(isinstance(item, dict) and isinstance(item.get('constraint'), str) and isinstance(item.get('source'), str) for item in evidence):
                raise TranslationValidationError('Expected constraint/source evidence objects')
            quotes = {item['constraint']: item['source'] for item in evidence}
            if len(quotes) != len(evidence) or len(set(codes)) != len(codes):
                raise TranslationValidationError('Duplicate constraints or evidence')
            for code in codes:
                try:
                    for normalized, source in self.normalize(code, quotes.get(code)):
                        if kind == 'hard' and normalized in self.locked and normalized not in quotes:
                            continue
                        fact = comparison(normalized)
                        if fact and fact[0] in self.derived:
                            if kind != 'hard':
                                raise TranslationValidationError('Derived metadata belongs in hard_logic')
                            continue
                        source = verbatim_source(self.query['nature_language'], source)
                        contexts = []
                        for atom, negated in constraint_atoms(normalized, with_polarity=True):
                            atom_fact = comparison(atom)
                            modality, context = source_modality(self.query['nature_language'], source, field=atom_fact[0])
                            contexts.append(context)
                            expected = 'soft' if modality in ('soft', 'negative_soft') else 'hard'
                            if negated != modality.startswith('negative'):
                                raise TranslationValidationError('Preserve the source negation and its scope')
                            if kind != expected:
                                raise TranslationValidationError('This source requirement belongs in ' + expected + '_logic')
                            if atom_fact[0] in ('cost', 'rooms', 'room_type', 'hotel_price', 'food_price'):
                                references = [comparison(entry['code']) for entry in self.checklist + self.preference_checklist if entry['field'] == atom_fact[0]]
                                if references and not any(type(reference[1]) == type(atom_fact[1]) and reference[2] == atom_fact[2] for reference in references):
                                    raise TranslationValidationError('Constraint conflicts with explicit source value/operator')
                        context = contexts[0]
                        grounded = dict(constraint=normalized, source=source)
                        _validate_extraction_items(self.query, dict(hard_logic=[normalized], hard_logic_evidence=[grounded]))
                        destination = candidate_hard if kind == 'hard' else candidate_soft
                        opposite = candidate_soft if kind == 'hard' else candidate_hard
                        if ast_key(normalized) in {ast_key(item) for item in opposite}:
                            raise TranslationValidationError('The same requirement cannot be both hard and soft')
                        destination[normalized] = dict(constraint=normalized, source=context)
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append(f'{kind} requirement {code}: {exc}')
        missing = [entry for entry in self.checklist if not self.covered(entry, candidate_hard)]
        if missing:
            errors.append('Missing explicit requirements: ' + json.dumps(missing, ensure_ascii=False))
        missing_preferences = [entry for entry in self.preference_checklist if not self.covered(entry, candidate_soft)]
        if missing_preferences:
            errors.append('Missing preferences in soft_logic: ' + json.dumps(missing_preferences, ensure_ascii=False))
        result = dict(parsed, hard_logic=list(candidate_hard), hard_logic_evidence=list(candidate_hard.values()),
                      soft_logic=list(candidate_soft), soft_logic_evidence=list(candidate_soft.values()),
                      source_coverage=self.checklist, unmapped_requirements=list(dict.fromkeys(unmapped)))
        dropped = inventory_coverage(self.query.get('requirement_inventory', []), result)
        if dropped:
            errors.append('Missing inventoried requirements; translate or retain verbatim in unmapped_requirements: ' + json.dumps(dropped, ensure_ascii=False))
        self.errors = errors
        if errors:
            raise TranslationValidationError('\n'.join(errors))
        self.locked = candidate_hard
        self.preferences = candidate_soft
        return result


def grounded_extraction(query,parsed):
    try:return ExtractionState(query).accept(parsed)
    except TranslationValidationError as exc:
        raise TranslationValidationError('Translation extraction validation: '+str(exc)) from exc

def extract_with_repair(query, llm, prompt, lang='zh'):
    inventory, inventory_repairs = collect_requirements(query, llm, lang=lang)
    working = deepcopy(query)
    working['requirement_inventory'] = inventory
    state = ExtractionState(working)
    inventory_prompt = '\nComplete source-grounded requirement inventory:\n' + json.dumps(inventory, ensure_ascii=False)
    hints = grounding_instructions(working, state.catalog)
    error = None
    for attempt in range(3):
        content = prompt + inventory_prompt + hints + state.instructions()
        if error:
            content += '\nRepair the listed errors or missing requirements; preserve validated items:\n' + str(error)
        response = llm([{'role': 'user', 'content': content}], one_line=False, json_mode=True,
                       **extraction_output_options(llm, include_unmapped=True))
        try:
            parsed = json.loads(response)
            if not isinstance(parsed, dict):
                raise TranslationValidationError('Extraction must be a JSON object')
            for key in ('uid', 'days', 'people_number', 'start_city', 'target_city'):
                if key in query and key in parsed and query[key] != parsed[key]:
                    raise TranslationValidationError('Extraction changed input field ' + key)
            result = state.accept(parsed)
            for key in ('hard_logic', 'hard_logic_evidence', 'source_coverage', 'soft_logic', 'soft_logic_evidence', 'unmapped_requirements'):
                query[key] = result[key]
            query['translation_flow_version'] = TRANSLATION_FLOW_VERSION
            query['requirement_inventory'] = inventory
            query['inventory_repair_count'] = inventory_repairs
            query['extraction_repair_count'] = attempt
            return query
        except (ValueError, TypeError, KeyError) as exc:
            error = exc
    raise TranslationValidationError('Translation stage 1 failed after 3 attempts: ' + str(error))
