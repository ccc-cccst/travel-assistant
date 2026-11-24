"""Separate JSON syntax, interface shape, DSL validity and reference agreement."""
import ast
import json

from task_data import CATALOGS, VOCABULARY, canonical_constraints

ALLOWED_VALUES = dict(VOCABULARY)
ALLOWED_VALUES.update(transport_type=['metro', 'taxi', 'walk'], intercity_transport=['train', 'airplane'])
for field in ('attraction_names', 'restaurant_names', 'hotel_names'):
    ALLOWED_VALUES[field] = {value for city in CATALOGS.values() for value in city[field]}


def valid_expression(expression):
    def visit(node):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            return all(visit(value) for value in node.values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return visit(node.operand)
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            return False
        if not isinstance(node.ops[0], (ast.LtE, ast.Eq, ast.NotEq)):
            return False
        left, right = node.left, node.comparators[0]
        if isinstance(left, ast.Name) and isinstance(right, ast.Set):
            left, right = right, left
        if not isinstance(left, ast.Set) or not isinstance(right, ast.Name):
            return False
        if right.id not in ALLOWED_VALUES:
            return False
        return bool(left.elts) and all(isinstance(value, ast.Constant) and isinstance(value.value, str)
                                       and value.value in ALLOWED_VALUES[right.id] for value in left.elts)
    try:
        return isinstance(expression, str) and visit(ast.parse(expression, mode='eval').body)
    except (SyntaxError, TypeError, ValueError):
        return False


def score_output(output, row):
    expected = canonical_constraints(row['answer']['soft_logic'])
    result = dict(json_valid=False, output_format_valid=False, dsl_valid=False, dsl_exact=False,
                  usable_exact=False, evidence_complete=False, true_positive=0, predicted=0, expected=len(expected))
    text = output.strip()
    if text.startswith('```') and '\n' in text:
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        answer = json.loads(text)
        result['json_valid'] = True
        fields = {'soft_logic', 'soft_logic_evidence', 'unmapped_requirements'}
        if not isinstance(answer, dict) or set(answer) != fields:
            raise ValueError('Expected the three output fields')
        if not all(isinstance(answer[field], list) for field in fields):
            raise ValueError('Output fields must be lists')
        if not all(isinstance(value, str) for value in answer['soft_logic'] + answer['unmapped_requirements']):
            raise ValueError('DSL and unmapped requirements must be strings')
        evidence = answer['soft_logic_evidence']
        if not all(isinstance(item, dict) and set(item) == {'constraint', 'source'}
                   and isinstance(item['constraint'], str) and isinstance(item['source'], str) for item in evidence):
            raise ValueError('Invalid evidence structure')
        result.update(output_format_valid=True, answer=answer,
                      dsl_valid=all(valid_expression(value) for value in answer['soft_logic']))
        predicted = canonical_constraints(answer['soft_logic'])
        evidence_complete = (
            all(item['source'] and item['source'] in row['input'] and item['constraint'] in answer['soft_logic'] for item in evidence)
            and {item['constraint'] for item in evidence} == set(answer['soft_logic'])
        )
        unmapped = answer['unmapped_requirements']
        gold_unmapped = row['answer']['unmapped_requirements']
        unmapped_covered = all(any(quote in item for item in unmapped) for quote in gold_unmapped)
        unmapped_grounded = all(item and item in row['input'] for item in unmapped)
        unmapped_correct = unmapped_covered and unmapped_grounded and bool(unmapped) == bool(gold_unmapped)
        result.update(dsl_exact=predicted == expected, evidence_complete=evidence_complete,
                      true_positive=len(predicted & expected), predicted=len(predicted),
                      unmapped_coverage_correct=unmapped_correct)
        result['usable_exact'] = result['dsl_valid'] and result['dsl_exact'] and evidence_complete and unmapped_correct
    except (ValueError, TypeError, KeyError, SyntaxError) as exc:
        result['error'] = str(exc)
    return result
