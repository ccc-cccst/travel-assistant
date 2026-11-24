"""The same input construction is used for training and evaluation."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOGS = json.loads((ROOT/'data/catalogs.json').read_text(encoding='utf-8'))
VOCABULARY = {field: sorted({value for city in CATALOGS.values() for value in city[field]})
              for field in ('food_type','spot_type','hotel_feature')}
CITY_CUISINES = {
    '上海': ['本帮菜'], '北京': ['北京菜'], '成都': ['川菜'], '重庆': ['川菜'],
    '武汉': ['湖北菜'], '广州': ['粤菜'], '深圳': ['粤菜', '客家菜'],
    '杭州': ['江浙菜'], '苏州': ['江浙菜'], '南京': ['江浙菜'],
}
SYSTEM = (
    '你是旅游软约束DSL翻译器。输出且仅输出一个JSON对象，三个字段始终存在。'
    'soft_logic是Python布尔表达式字符串列表，每个元素必须是字符串。'
    'soft_logic_evidence是包含constraint和source两个字符串的对象列表。'
    'unmapped_requirements是无法完整映射的原文片段字符串列表。空列表写[]，不输出额外字段或Markdown。\n'
    'soft_logic记录必须满足的用户愿望。类型、地点、交通体验默认全程至少出现一次，'
    '统一写集合包含表达式，如{\'metro\'}<=transport_type。不能写transport_type==metro，'
    '也不能把至少出现一次扩大为全程只允许该方式。标签来自下列词表，地点使用数据库中的正式名称。\n'
    '多个都要分别满足，任选一个使用or，否定要求使用not。先确定否定、并且、或者各自的作用范围，'
    '用括号保留组合逻辑。每一个仍然有效的愿望都要覆盖，最后一项不得覆盖前面的其他有效愿望。'
    '区分明确禁止、撤销要求和背景经历，不把过去或他人的经历当作当前要求。\n'
    '先根据整段原文确定实际游玩目的地，再解析当地菜、那里、前者和后者。'
    '出发地和中转地不得代替游玩目的地。城市名只用于理解上下文，不作为景点要求。'
    '项目当地菜映射为'+json.dumps(CITY_CUISINES,ensure_ascii=False,separators=(',',':'))+'。'
    '同城有多个当地菜标签时保留任选关系。普通设施描述应映射到已有设施标签，不能因为缺少标签原词就遗漏需求。\n'
    '条件要求不能改成无条件要求。现有DSL没有天气或空闲条件运算，带这类条件的完整需求放入unmapped_requirements。'
    '频次或修饰属性超出DSL时，映射可以独立成立的类型部分，并在unmapped_requirements保留未表达的完整限定。'
    '只有位置关系而没有参观意图时，不得生成参观地标的要求。不得编造阈值、菜单或未标注属性。\n'
    '字段为food_type、spot_type、hotel_feature、attraction_names、restaurant_names、hotel_names、'
    'transport_type、intercity_transport。市内方式仅metro/taxi/walk，城际仅train/airplane。'
    '每个DSL必须有逐字原文证据，证据中的constraint必须与soft_logic中的字符串完全一致。\n'
    '类型与设施词表为'+json.dumps(VOCABULARY,ensure_ascii=False,separators=(',',':'))
)

# This demonstration is drawn exclusively from the frozen training partition.
DEMONSTRATION = json.loads((ROOT/'data/train.jsonl').read_text(encoding='utf-8').splitlines()[0])
SYSTEM += ('\n完整输出格式示例。输入为'+DEMONSTRATION['input']+'，输出为'
           +json.dumps(DEMONSTRATION['answer'],ensure_ascii=False,separators=(',',':')))

def load_rows(split):
    return [json.loads(line) for line in (ROOT/'data'/f'{split}.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]

def messages_for(row):
    return [dict(role='system',content=SYSTEM),dict(role='user',content=row['input'])]

def tokenize_row(tokenizer,row,max_length):
    messages=messages_for(row)
    prefix=tokenizer.apply_chat_template(messages,tokenize=True,return_dict=False,add_generation_prompt=True,enable_thinking=False)
    answer=json.dumps(row['answer'],ensure_ascii=False,separators=(',',':'))
    answer_messages=messages+[dict(role='assistant',content=answer)]
    full=tokenizer.apply_chat_template(answer_messages,
        tokenize=True,return_dict=False,add_generation_prompt=False,enable_thinking=False)
    if full[:len(prefix)]!=prefix:
        raise ValueError('Assistant boundary mismatch '+row['id'])
    if len(full)>max_length:
        raise ValueError(f'{row["id"]} requires {len(full)} tokens, max_length={max_length}; no truncation allowed')
    rendered=tokenizer.apply_chat_template(answer_messages,tokenize=False,add_generation_prompt=False,enable_thinking=False)
    encoding=tokenizer(rendered,add_special_tokens=False,return_offsets_mapping=True)
    if encoding['input_ids']!=full:
        raise ValueError('Character offsets differ from chat tokens '+row['id'])
    answer_start=rendered.rfind(answer)
    if answer_start<0:
        raise ValueError('Answer not found in rendered chat')
    config=json.loads((ROOT/'config.json').read_text())
    spans=[]
    for field,weight in [('soft_logic',config['dsl_loss_weight']),('unmapped_requirements',config['unmapped_loss_weight'])]:
        value_start=answer.index('"'+field+'":')+len(field)+3
        _,value_end=json.JSONDecoder().raw_decode(answer,value_start)
        spans.append((answer_start+value_start,answer_start+value_end,weight))
    weights=[]
    for index,(start,end) in enumerate(encoding['offset_mapping']):
        weight=0.0 if index<len(prefix) else 1.0
        if weight:
            for span_start,span_end,span_weight in spans:
                if start<span_end and end>span_start:
                    weight=max(weight,span_weight)
        weights.append(weight)
    return dict(input_ids=full,attention_mask=[1]*len(full),labels=[-100]*len(prefix)+full[len(prefix):],loss_weights=weights)

def canonical_constraints(expressions):
    def normalize(node):
        if isinstance(node,ast.Set):
            return 'set('+','.join(sorted(normalize(value) for value in node.elts))+')'
        if isinstance(node,ast.Constant):
            return repr(node.value)
        if isinstance(node,ast.Name):
            return node.id
        if isinstance(node,ast.BoolOp):
            return type(node.op).__name__+'('+','.join(sorted(normalize(value) for value in node.values))+')'
        if isinstance(node,ast.Compare):
            return normalize(node.left)+''.join(type(operator).__name__+normalize(right) for operator,right in zip(node.ops,node.comparators))
        if isinstance(node,ast.UnaryOp):
            return type(node.op).__name__+'('+normalize(node.operand)+')'
        raise ValueError('Unsupported expression '+ast.dump(node))
    result=set()
    for expression in expressions:
        node=ast.parse(expression,mode='eval').body
        # A conjunction of positive set-membership requirements is order independent.
        if isinstance(node,ast.Compare) and len(node.ops)==1 and isinstance(node.ops[0],ast.LtE) and isinstance(node.left,ast.Set) and isinstance(node.comparators[0],ast.Name):
            for value in node.left.elts:
                result.add(normalize(ast.Compare(left=ast.Set(elts=[value]),ops=[ast.LtE()],comparators=node.comparators)))
        else:
            result.add(normalize(node))
    return result
