"""Ground extracted requirements and compile the existing standard vocabulary."""
import ast
import math
import re
from .translation_contract import TranslationValidationError
from .semantic_grounding import category_supported

EXTRACTION_CONTRACT = '''
Extraction validation (overrides examples): Do not invent constraints or use 0
for unspecified budgets, rooms or bed types. Leave unspecified fields absent/null.
For every non-derived hard_logic item add hard_logic_evidence containing
{"constraint":"the exact hard_logic string", "source":"verbatim user quote"}.
days/people_number may come from input metadata; tickets and taxi_cars may be
derived from people_number. Rooms and room_type may NOT be derived from people.
Distinguish attraction_names/spot_type and restaurant_names/food_type: named
places are names, not categories. Do not invent a cuisine category named 美食
for a vague request such as "尽可能多体验当地美食"; retain unsupported requests verbatim.
Preserve all explicit requirements, including specific named places and modes.
Separate hard_logic from soft_logic before generating any checking code.
In this project both hard_logic and soft_logic record requirements that the final
itinerary must satisfy. The name soft_logic identifies user-semantic requirements;
it does not grant permission to omit them, merely rank matching options higher,
or trade a missing requirement against a better total score.
Preserve concrete requests expressed with 想去/想吃/希望/喜欢/want/would like
as explicit, verifiable requirements even when the user does not say 必须/must.
For a requested food tag, require at least one scheduled meal at a restaurant whose
food_type includes that tag. For example, 想吃烧烤 means {'烧烤'}<=food_type.
A restaurant name containing 烧烤 alone does not establish its cuisine tag.
For a requested attraction, require at least one actual visit to that attraction;
for several requested cuisine tags require each tag to occur during the trip.
Apply existence across the trip unless the user explicitly says every meal/day.
Do not turn 想吃烧烤 into eating barbecue at every meal. Preserve explicit counts,
negations, OR alternatives and conditions. Do not invent a numerical threshold for
vague goals such as a relaxed pace. Preserve a request verbatim in
unmapped_requirements when the available DSL cannot express it faithfully.

Wishes such as 想去/想吃/想住/可以去/希望/喜欢/最好/尽量 and
want/would like/prefer/can visit are soft. Explicit 必须/只能/must obligations
in the same requirement override wish wording. Numeric limits and room counts
remain hard, including polite 希望预算不超过3000. Preferably/尽量 limiting a
value retains its original qualifier in soft_logic; do not invent a strict bound. Preserve complete clause evidence and separate
adjacent requirements so modifiers do not cross scopes. Keep exclusions with
not on the named/type membership expression, one excluded value per item.
不想去故宫 is a hard exclusion; 最好不去故宫 is a soft exclusion.
Keep OR alternatives rather than converting them into simultaneous requirements.
Use destination context to interpret 当地菜/local cuisine and the installed
food_type catalog. Match food preferences to restaurant cuisine, not its name.
Preserve inventoried requests with no faithful DSL representation verbatim in
unmapped_requirements when requested by the schema. Never invent symbols such
as n or min_price, or replace an unsupported goal with a bare field name.
Explicit room counts and bed counts are independent: "三间大床房" means
rooms==3 and room_type==1; "住大床房" specifies room_type==1 but no room count.
"双床房" means room_type==2. "单人房" and "双人间" describe occupancy,
not a proven bed count; preserve explicit room counts without inventing beds.
For example, "住一间有桑拿的酒店，双人间就行" requires rooms==1 and
{'桑拿'}<=hotel_feature. Do NOT emit room_type==2: two occupants do not prove
two beds. Keep the occupancy wording in the original query.
Use numeric equality for rooms/room_type, never a set of room-name strings.
When the local JSON schema requests only four logic/evidence arrays, emit
the requested arrays, including unmapped_requirements if requested. City/day/people metadata already exists in the input.
Do not invent symbolic start_city/target_city set-membership constraints.
Start/destination city names are context, never attraction_names or restaurant_names.
If there are no additional requirements, the logic arrays may be empty. The local
validator inserts authoritative days/people/tickets/taxi counts automatically.
For unmapped_requirements, COPY inventory source, NEVER inventory requirement.
Example inventory {"source":"希望行程轻松一些","requirement":"轻松游玩"} maps to
unmapped_requirements=["希望行程轻松一些"]. Do not paraphrase source quotes.
If extraction is rejected, correct the extraction itself, not Python code.
'''

def comparison(text):
    try:
        node=ast.parse(text,mode='eval').body
        if not isinstance(node,ast.Compare) or len(node.ops)!=1:return None
        left,right=node.left,node.comparators[0]
        if isinstance(left,ast.Name):return left.id,node.ops[0],ast.literal_eval(right),'scalar'
        if isinstance(right,ast.Name):return right.id,node.ops[0],ast.literal_eval(left),'set'
    except (ValueError,SyntaxError,TypeError):pass
    return None

def _catalog(city):
    from chinatravel.symbol_verification.concept_func import _tools_for_lang
    from chinatravel.environment.language import CITY_NAMES
    lang=next((lang for lang in ('zh','en') if city in CITY_NAMES[lang]),None)
    if lang is None:raise TranslationValidationError('Unknown target city for extraction: '+str(city))
    tools=_tools_for_lang(lang)
    result={}
    for tool,names,types,column in (
        ('attractions','attraction_names','spot_type','type'),
        ('restaurants','restaurant_names','food_type','cuisine'),
        ('accommodations','hotel_names','hotel_feature','featurehoteltype')):
        rows=tools[tool].data[city]
        result[names]=set(rows['name'].dropna().astype(str))
        result[types]=set(rows[column].dropna().astype(str))
    return result

def _norm(value):return re.sub(r'\s+','',value).lower()

def chinese_number(token):
    digits = {'零':0, '一':1, '二':2, '两':2, '三':3, '四':4, '五':5,
              '六':6, '七':7, '八':8, '九':9}
    total = part = current = 0
    for char in token:
        if char in digits:
            current = digits[char]
        elif char == '万':
            total += (part + current) * 10000
            part = current = 0
        else:
            part += (current or 1) * {'十':10, '百':100, '千':1000}[char]
            current = 0
    return total + part + current


def _number_present(value,source):
    numbers=[float(x)*{'':1,'万':10000,'千':1000}[unit]
             for x,unit in re.findall(r'(\d+(?:\.\d+)?)\s*([万千]?)',source)]
    if value in numbers:return True
    # Common Chinese number forms used for room counts and prices.
    for token in re.findall('[零一二两三四五六七八九十百千万]+',source):
        if chinese_number(token)==value:return True
    words={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
    for word,unit in re.findall(r'\b(one|two|three|four|five|six|seven|eight|nine|ten)\b(?:\s+(hundred|thousand))?',source,re.I):
        if words[word.lower()]*{'':1,'hundred':100,'thousand':1000}[unit.lower()]==value:return True
    return False

SET_FIELDS = {
    'attraction_names', 'restaurant_names', 'hotel_names',
    'spot_type', 'food_type', 'hotel_feature',
}
MODE_VALUES = {
    'transport_type': {'metro', 'taxi', 'walk'},
    'intercity_transport': {'train', 'airplane'},
    'train_type': {'G', 'D', 'C', 'Z', 'T', 'K', 'S', 'Y', 'L', 'A'},
}
NUMERIC_FIELDS = {'days', 'people_number', 'tickets', 'taxi_cars', 'cost',
                  'rooms', 'room_type', 'hotel_price', 'food_price', 'activity_time'}
TIME_FIELDS = {'activity_start_time', 'activity_end_time', 'return_start_time'}


def constraint_atoms(code, with_polarity=False):
    """Validate expression shape and return comparisons, preserving boolean structure."""
    try:
        root = ast.parse(code, mode='eval').body
    except (SyntaxError, TypeError) as exc:
        raise TranslationValidationError('Invalid symbolic expression: ' + str(code)) from exc

    def visit(node, negated=False):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.Or, ast.And)):
            return [atom for child in node.values for atom in visit(child, negated)]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            atoms = visit(node.operand, not negated)
            if len(atoms) != 1:
                raise TranslationValidationError('Use one explicit excluded item per expression')
            fact = comparison(atoms[0][0])
            if fact[0] not in SET_FIELDS | set(MODE_VALUES) or fact[3] != 'set' or not isinstance(fact[2], set) or len(fact[2]) != 1 or not isinstance(fact[1], ast.LtE):
                raise TranslationValidationError('Exclusion requires a single named/type membership expression')
            return atoms
        if not isinstance(node, ast.Compare):
            raise TranslationValidationError('A requirement needs a comparison, not a bare symbol')
        atom = ast.unparse(node)
        fact = comparison(atom)
        if fact is None or fact[0] not in SET_FIELDS | set(MODE_VALUES) | NUMERIC_FIELDS | TIME_FIELDS:
            raise TranslationValidationError('Unknown field or nonliteral comparison in ' + atom)
        if not isinstance(fact[1], (ast.Eq, ast.NotEq, ast.LtE, ast.Lt, ast.GtE, ast.Gt)):
            raise TranslationValidationError('Unsupported comparison operator in ' + atom)
        if fact[0] in ('days', 'people_number', 'tickets', 'taxi_cars') and not isinstance(root, ast.Compare):
            raise TranslationValidationError('Derived metadata must remain separate from boolean requirements')
        return [(atom, negated)]

    terms = visit(root)
    return terms if with_polarity else [atom for atom, negated in terms]


NUMERIC_OPERATORS = (
    (ast.LtE, r'不超过|不要超过|不能超过|不得超过|不得高于|不高于|至多|最多|以内|以下|at most|no more than'),
    (ast.GtE, r'不少于|不低于|至少|最少|at least|no less than'),
    (ast.Lt, r'低于|小于|少于|less than|under'),
    (ast.Gt, r'高于|大于|多于|more than'),
)
NUMERIC_SCOPES = {
    'cost': r'预算|总费用|总花费|总消费|总价|花费|花销|开销|费用|budget|total cost',
    'hotel_price': r'酒店|住宿|房费|每晚|hotel|accommodation|per night',
    'food_price': r'餐|饭|每顿|meal|food|dining',
}


def numeric_source_clause(field, source):
    if field not in NUMERIC_SCOPES:
        return source
    parts = re.split(r'[，,。;；\n]|并且|而且|且|以及|\band\b', source, flags=re.I)
    candidates = [part for part in parts if re.search(NUMERIC_SCOPES[field], part, re.I)]
    if len(candidates) != 1:
        raise TranslationValidationError('Quote a single numeric scope for ' + field)
    selected = candidates[0]
    other_fields = [other for other, pattern in NUMERIC_SCOPES.items()
                    if other != field and re.search(pattern, selected, re.I)]
    # A hotel/meal budget may say budget too; another price scope needs a separate quote.
    competing = [other for other in other_fields if other != 'cost']
    if competing:
        raise TranslationValidationError('Separate the budget, hotel and meal source scopes')
    return re.sub(r'(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百千万]+)\s*(?:个人|人(?!民币)|位|名)', '', selected)


def numeric_source_operator(field, source):
    source = numeric_source_clause(field, source)
    if field == 'room_type' and re.search(r'单床|大床|双床|\btwin\b|\b(?:king|queen|double|single)[ -]bed\b', source, re.I):
        return ast.Eq
    for operator, pattern in NUMERIC_OPERATORS:
        if re.search(pattern, source, re.I):
            return operator
    if field in ('rooms', 'room_type') or re.search(r'正好|恰好|等于|exactly', source, re.I):
        return ast.Eq
    return ast.LtE


MODE_SOURCES = {
    'metro': r'地铁|公共交通|metro|subway|public transport',
    'taxi': r'打车|出租车|taxi|cab',
    'walk': r'步行|走路|walk',
    'train': r'火车|高铁|动车|train|rail',
    'airplane': r'飞机|航班|飞往|飞到|飞去|飞|flight|fly|plane',
}


def _validate_extraction_items(query, parsed):
    from .extraction_alignment import aliases
    codes = parsed.get('hard_logic', [])
    quotes = {item.get('constraint'): item.get('source') for item in parsed.get('hard_logic_evidence', []) if isinstance(item, dict)}
    text = query['nature_language']
    catalog = None
    evidence = []
    derived = {'days': query.get('days'), 'people_number': query.get('people_number'),
               'tickets': query.get('people_number')}
    if query.get('people_number') is not None:
        derived['taxi_cars'] = (query['people_number'] + 3) // 4
    for code in codes:
        atoms = constraint_atoms(code)
        fact = comparison(code)
        if fact and fact[0] in derived and fact[3] == 'scalar' and isinstance(fact[1], ast.Eq):
            if isinstance(fact[2], bool) or fact[2] != derived[fact[0]]:
                raise TranslationValidationError('Extraction conflicts with input people/day counts')
            evidence.append(dict(constraint=code, source='input metadata / passenger-count derivation', derived=True))
            continue
        source = quotes.get(code)
        if not isinstance(source, str) or not source.strip() or _norm(source) not in _norm(text):
            raise TranslationValidationError('Missing verbatim source evidence for ' + code)
        for atom in atoms:
            field, op, value, shape = comparison(atom)
            if field in NUMERIC_FIELDS:
                numeric_source = numeric_source_clause(field, source)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                    raise TranslationValidationError('Numeric constraints need a positive literal: ' + atom)
                if field in ('cost', 'hotel_price', 'food_price', 'rooms', 'room_type'):
                    cue = (r'间|rooms?\b' if field == 'rooms' else r'床|beds?\b|\btwin\b|\bking\b|\bqueen\b' if field == 'room_type'
                           else r'预算|元|人民币|块|¥|￥|budget|yuan|rmb|cost|price|cny|每顿|每餐|每晚|一晚|房价|酒店价格')
                    if field in ('hotel_price', 'food_price') and re.search(r'分钟|小时|秒|千卡|公斤|\bkg\b', numeric_source, re.I) and not re.search(r'元|人民币|块|¥|￥|yuan|rmb|cny', numeric_source, re.I):
                        raise TranslationValidationError('A time or quantity bound is not a price')
                    if not re.search(cue, numeric_source, re.I):
                        raise TranslationValidationError('Source does not request this numeric field: ' + atom)
                    if not isinstance(op, numeric_source_operator(field, numeric_source)):
                        raise TranslationValidationError('Numeric operator conflicts with its source: ' + atom)
                    beds = set()
                    if field == 'room_type':
                        if re.search(r'单床|大床|\b(?:single|double|king|queen)[ -]bed\b', source, re.I):
                            beds.add(1)
                        if re.search(r'双床|\btwin\b|\btwo beds\b', source, re.I):
                            beds.add(2)
                        if beds and beds != {value}:
                            raise TranslationValidationError('Bed count must match explicit bed wording')
                    if not beds and not _number_present(value, numeric_source):
                        raise TranslationValidationError('Numeric value is not supported by source: ' + atom)
            if field == 'activity_time':
                minutes = re.search(r'分钟|minutes?', source, re.I)
                hours = re.search(r'小时|hours?', source, re.I)
                supported = (minutes and _number_present(value, source)) or (hours and _number_present(value / 60, source))
                if not supported:
                    raise TranslationValidationError('Duration must be supported by an explicit source amount and unit')
            if field in TIME_FIELDS:
                if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
                    raise TranslationValidationError('Time constraints require an HH:MM literal')
                hour, minute = map(int, value.split(':'))
                exact_clock = value in _norm(source)
                period_hour = hour - 12 if hour > 12 and re.search(r'下午|晚上|傍晚|夜|\bpm\b|afternoon|evening', source, re.I) else hour
                named_clock = (re.search(r'点|时|\bhours?\b|\bam\b|\bpm\b', source, re.I)
                               and _number_present(period_hour, source)
                               and (minute == 0 or _number_present(minute, source)))
                if not exact_clock and not named_clock:
                    raise TranslationValidationError('Clock bound must be supported by an explicit source time')
            if field in SET_FIELDS | set(MODE_VALUES):
                if not isinstance(value, set) or not value or not all(isinstance(item, str) for item in value):
                    raise TranslationValidationError('Expected a nonempty set of names/types: ' + atom)
                if field in MODE_VALUES:
                    if not value <= MODE_VALUES[field]:
                        raise TranslationValidationError('Unknown transport mode or train prefix: ' + atom)
                    if field != 'train_type' and not all(re.search(MODE_SOURCES[mode], source, re.I) for mode in value):
                        raise TranslationValidationError('Transport mode is not requested by its source: ' + atom)
                    continue
                if catalog is None:
                    catalog = _catalog(query.get('target_city', parsed.get('target_city')))
                for item in value:
                    if item not in catalog[field]:
                        raise TranslationValidationError(f'{item!r} is not a valid {field} in the target city')
                    if field.endswith('_names'):
                        matched = any(item in options and _norm(alias) in _norm(source) for alias, options in aliases(catalog[field]).items())
                        if not matched:
                            raise TranslationValidationError('Named place is not supported by its source: ' + atom)
                    elif not category_supported(query, catalog, field, item, source):
                        raise TranslationValidationError('Category/feature is not supported by its source: ' + atom)
        evidence.append(dict(constraint=code, source=source, derived=False))
    result = dict(parsed)
    result['hard_logic_evidence'] = evidence
    return result


def validate_extraction(query,parsed):
    from .extraction_alignment import grounded_extraction
    return grounded_extraction(query,parsed)

def compile_constraint(text):
    try:tree=ast.parse(text,mode='eval').body
    except SyntaxError:return None
    if isinstance(tree, ast.UnaryOp) and isinstance(tree.op, ast.Not):
        try:
            constraint_atoms(text)
        except TranslationValidationError:
            return None
        program = compile_constraint(ast.unparse(tree.operand))
        return program + '\nresult=(not result)' if program is not None else None
    if isinstance(tree,ast.BoolOp) and isinstance(tree.op,ast.Or):
        options = [comparison(ast.unparse(node)) for node in tree.values]
        if all(option and option[0] == options[0][0] and option[0] in SET_FIELDS
               and option[3] == 'set' and isinstance(option[1], ast.LtE)
               and isinstance(option[2], set) and option[2]
               and all(isinstance(value, str) for value in option[2]) for option in options):
            field = options[0][0]
            kinds = {'attraction_names': ['attraction'], 'spot_type': ['attraction'],
                     'restaurant_names': ['breakfast', 'lunch', 'dinner'], 'food_type': ['breakfast', 'lunch', 'dinner'],
                     'hotel_names': ['accommodation'], 'hotel_feature': ['accommodation']}
            function = {'spot_type': 'attraction_type', 'food_type': 'restaurant_type', 'hotel_feature': 'accommodation_type'}.get(field)
            expression = f'{function}(activity,target_city(plan))' if function else 'activity_position(activity)'
            clauses = ['(' + repr(option[2]) + '<=seen)' for option in options]
            return (f'seen=set()\nfor activity in allactivities(plan):\n  if activity_type(activity) in {kinds[field]!r}: seen.add({expression})'
                    + '\nresult=(' + ' or '.join(clauses) + ')')
    fact=comparison(text)
    if not fact:return None
    field,op,value,shape=fact
    symbols={ast.Eq:'==',ast.LtE:'<=',ast.Lt:'<',ast.GtE:'>=',ast.Gt:'>'}
    symbol=symbols.get(type(op))
    if field=='return_start_time' and shape=='scalar' and isinstance(op,(ast.LtE,ast.Lt)) and isinstance(value,str):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):return None
        return "found=False\nresult=True\nfor activity in allactivities(plan):\n  if activity_type(activity) in ['train','airplane'] and intercity_transport_origin(activity)==target_city(plan):\n    found=True\n    if not (activity_start_time(activity)"+symbol+repr(value)+"): result=False\nresult=(found and result)"
    if shape=='scalar' and isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):
        if field in ('days','people_number') and isinstance(op,ast.Eq):
            return f'result=({"day_count" if field=="days" else "people_count"}(plan)=={value!r})'
        if field=='cost' and symbol:
            return 'total_cost=0\nfor activity in allactivities(plan):\n  total_cost+=activity_cost(activity)+innercity_transport_cost(activity_transports(activity))\nresult=(total_cost'+symbol+repr(value)+')'
        if field in ('rooms','room_type','hotel_price','food_price') and symbol:
            fn={'rooms':'room_count','room_type':'room_type','hotel_price':'activity_price','food_price':'activity_price'}[field]
            kinds=['breakfast','lunch','dinner'] if field=='food_price' else ['accommodation']
            return f'result=True\nfor activity in allactivities(plan):\n  if activity_type(activity) in {kinds!r} and not ({fn}(activity){symbol}{value!r}): result=False'
        if field=='tickets' and isinstance(op,ast.Eq):
            return f"result=True\nfor activity in allactivities(plan):\n  if activity_type(activity) in ['attraction','train','airplane'] and activity_tickets(activity)!={value!r}: result=False\n  for leg in activity_transports(activity):\n    if leg['mode']=='metro' and metro_tickets(activity_transports(activity))!={value!r}: result=False"
        if field=='taxi_cars' and isinstance(op,ast.Eq):
            return f"result=True\nfor activity in allactivities(plan):\n  for leg in activity_transports(activity):\n    if leg['mode']=='taxi' and taxi_cars(activity_transports(activity))!={value!r}: result=False"
    if not isinstance(value,set) or not value or not all(isinstance(v,str) for v in value):return None
    literal='{'+', '.join(repr(v) for v in sorted(value))+'}'
    if shape=='set' and field=='intercity_transport' and isinstance(op,ast.LtE):
        return "modes=set()\nfor activity in allactivities(plan):\n  if activity_type(activity) in ['train','airplane']: modes.add(activity_type(activity))\nresult=("+literal+'<=modes)'
    if shape == 'set' and field == 'transport_type' and isinstance(op, ast.LtE):
        return "modes=set()\nfor activity in allactivities(plan):\n  if activity_transports(activity): modes.add(innercity_transport_type(activity_transports(activity)))\nresult=(" + literal + '<=modes)'
    if shape=='scalar' and field=='transport_type' and isinstance(op,(ast.LtE,ast.Eq)):
        return "modes=set()\nfor activity in allactivities(plan):\n  if activity_transports(activity): modes.add(innercity_transport_type(activity_transports(activity)))\nresult=(modes"+symbol+literal+')'
    if shape=='scalar' and field=='intercity_transport' and isinstance(op,ast.Eq):
        return "modes=set()\nfor activity in allactivities(plan):\n  if activity_type(activity) in ['train','airplane']: modes.add(activity_type(activity))\nresult=(modes=="+literal+')'
    if shape=='scalar' and field=='train_type' and isinstance(op,ast.Eq):
        return "found=False\nresult=True\nfor activity in allactivities(plan):\n  if activity_type(activity)=='train':\n    found=True\n    if activity['TrainID'][0] not in "+literal+": result=False\nresult=(found and result)"
    kinds={'attraction_names':['attraction'],'spot_type':['attraction'],
           'restaurant_names':['breakfast','lunch','dinner'],'food_type':['breakfast','lunch','dinner'],
           'hotel_names':['accommodation'],'hotel_feature':['accommodation']}
    if shape=='set' and isinstance(op,ast.LtE) and field in kinds:
        fn={'spot_type':'attraction_type','food_type':'restaurant_type','hotel_feature':'accommodation_type'}.get(field)
        expression=f'{fn}(activity,target_city(plan))' if fn else 'activity_position(activity)'
        return f'seen=set()\nfor activity in allactivities(plan):\n  if activity_type(activity) in {kinds[field]!r}: seen.add({expression})\nresult=({literal}<=seen)'
    return None

def template_programs(query):return [compile_constraint(c) for c in query['hard_logic']]
