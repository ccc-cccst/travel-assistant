"""The same input construction is used for training and evaluation."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOGS = json.loads((ROOT/'data/catalogs.json').read_text(encoding='utf-8'))
VOCABULARY = {field: sorted({value for city in CATALOGS.values() for value in city[field]})
              for field in ('food_type','spot_type','hotel_feature')}
SYSTEM = (
    '将旅游原文中的软约束翻译为DSL，只输出JSON，包含soft_logic、soft_logic_evidence、unmapped_requirements。'
    'soft_logic中的愿望必须满足，正向标签或地点默认全程至少一次。多个都要用集合包含，任选一个用or，排除用not。'
    '区分当前意图与过去经历、他人经历和已撤销意图，利用上下文解析当地菜、前者、后者等指代。'
    '保留条件、频次、范围，不编造阈值。现有DSL或数据库不支持的限定放入unmapped_requirements，能映射的部分仍输出。'
    '证据格式为{"constraint":"对应DSL","source":"逐字原文"}，证据必须来自用户原文。'
    '示例DSL为{\'烧烤\'}<=food_type、{\'泳池\'}<=hotel_feature、{\'公园\'}<=spot_type。'
    '地点使用attraction_names、restaurant_names、hotel_names。市内方式transport_type使用metro/taxi/walk，'
    '城际intercity_transport使用train/airplane。不得根据模型常识声称某店供应具体菜品或具有未标注设施。'
    '可用标签词表为'+json.dumps(VOCABULARY,ensure_ascii=False,separators=(',',':'))
)

def load_rows(split):
    return [json.loads(line) for line in (ROOT/'data'/f'{split}.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]

def messages_for(row):
    return [dict(role='system',content=SYSTEM),dict(role='user',content=row['input'])]

def tokenize_row(tokenizer,row,max_length):
    messages=messages_for(row)
    prefix=tokenizer.apply_chat_template(messages,tokenize=True,return_dict=False,add_generation_prompt=True,enable_thinking=False)
    full=tokenizer.apply_chat_template(messages+[dict(role='assistant',content=json.dumps(row['answer'],ensure_ascii=False,separators=(',',':')))],
        tokenize=True,return_dict=False,add_generation_prompt=False,enable_thinking=False)
    if full[:len(prefix)]!=prefix:
        raise ValueError('Assistant boundary mismatch '+row['id'])
    if len(full)>max_length:
        raise ValueError(f'{row["id"]} requires {len(full)} tokens, max_length={max_length}; no truncation allowed')
    return dict(input_ids=full,attention_mask=[1]*len(full),labels=[-100]*len(prefix)+full[len(prefix):])

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
