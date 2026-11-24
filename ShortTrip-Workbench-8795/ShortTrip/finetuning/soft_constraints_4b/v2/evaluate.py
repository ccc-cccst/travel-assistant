"""Compare the base and both adapters with the same revised prompt."""
from contextlib import nullcontext
import html
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
os.environ.update(HF_HOME=str(ROOT/'cache/huggingface'), TOKENIZERS_PARALLELISM='false')
from model_runtime import use_reference_attention
from scoring import score_output
from task_data import load_rows, messages_for
from training_protection import atomic_json

GROUPS = [('base_bf16', '原始4B', None), ('lora_v1_bf16', '第一轮LoRA', 'v1'), ('lora_v2_bf16', '第二轮LoRA', 'v2')]


def build_report(records, output):
    summary = {}
    for group, _, _ in GROUPS:
        selected = [row for row in records if row['group'] == group]
        counts = {name: sum(row['metrics'][name] for row in selected) for name in
                  ('json_valid', 'output_format_valid', 'dsl_valid', 'dsl_exact', 'usable_exact', 'evidence_complete')}
        true_positive = sum(row['metrics']['true_positive'] for row in selected)
        predicted = sum(row['metrics']['predicted'] for row in selected)
        expected = sum(row['metrics']['expected'] for row in selected)
        summary[group] = dict(total=len(selected), **counts,
            precision=true_positive/predicted if predicted else 0,
            recall=true_positive/expected if expected else 1,
            per_difficulty={level: dict(total=sum(row['difficulty'] == level for row in selected),
                exact=sum(row['difficulty'] == level and row['metrics']['dsl_exact'] for row in selected))
                for level in ('simple', 'moderate', 'very_vague')})
    atomic_json(output/'summary.json', summary)
    headers = ['<table><tr><th>指标</th>' + ''.join('<th>'+label+'</th>' for _, label, _ in GROUPS) + '</tr>']
    for label, key in [('JSON语法合法', 'json_valid'), ('接口结构正确', 'output_format_valid'),
                       ('DSL字段及词表合法', 'dsl_valid'), ('DSL整条匹配', 'dsl_exact'),
                       ('DSL、证据和未映射覆盖联合匹配', 'usable_exact')]:
        headers.append('<tr><td>'+label+'</td>'+''.join('<td>'+str(summary[group][key])+' / 30</td>' for group, _, _ in GROUPS)+'</tr>')
    headers.append('</table>')
    cards = []
    for gold in load_rows('test'):
        cards.append('<article><h2>'+gold['id']+' · '+gold['difficulty']+'</h2><p>'+html.escape(gold['input'])+'</p><h3>标准答案</h3><pre>'+html.escape(json.dumps(gold['answer'], ensure_ascii=False, indent=2))+'</pre><div class="outputs">')
        for group, label, _ in GROUPS:
            row = next(item for item in records if item['group'] == group and item['id'] == gold['id'])
            cards.append('<section><h3>'+label+'</h3><p>DSL整条匹配 '+('通过' if row['metrics']['dsl_exact'] else '未通过')+' · '+str(round(row['seconds'], 1))+'秒</p><pre>'+html.escape(row['raw_output'])+'</pre></section>')
        cards.append('</div></article>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第二轮BF16 LoRA回归评测</title><style>body{font:15px/1.7 "Microsoft YaHei",sans-serif;max-width:1600px;margin:32px auto;padding:0 20px;color:#26382e;background:#f4f6f4}article{padding:24px;margin:24px 0;border:1px solid #d7dfd7;background:white}.outputs{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:14px;background:#f6f7f5;font:13px/1.7 Consolas,monospace}table{border-collapse:collapse;background:white}td,th{border:1px solid #ccd6cc;padding:10px 20px;text-align:left}@media(max-width:1100px){.outputs{grid-template-columns:1fr}}</style><h1>第二轮BF16 LoRA回归评测</h1><p>三组使用同一新版提示词。30条数据曾用于第一轮错误分析，本次结果为回归评测。第一轮旧提示词的历史DSL整条匹配为22/30，提示词变化会影响结果，当前同提示词三组用于比较。</p><p>第二轮提高DSL与未映射字段的损失权重，以30条验证数据的生成DSL匹配率选择检查点。自动指标依据现有标注，语义正确性仍需逐条复核。</p>'+''.join(headers)+''.join(cards)+'</html>'
    (output/'comparison.html').write_text(page, encoding='utf-8')


def main():
    import torch
    from transformers import AutoTokenizer, Qwen3_5ForCausalLM
    from peft import PeftModel
    use_reference_attention()
    config = json.loads((ROOT/'config.json').read_text())
    output = ROOT/'evaluation'
    output.mkdir(exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(ROOT/config['model_dir'], local_files_only=True)
    base = Qwen3_5ForCausalLM.from_pretrained(ROOT/config['model_dir'], local_files_only=True,
        dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa')
    model = PeftModel.from_pretrained(base, ROOT / config['previous_adapter'], adapter_name='v1')
    model.load_adapter(ROOT/config['output_dir']/'adapter', adapter_name='v2')
    results = []
    for group, _, adapter in GROUPS:
        if adapter:
            model.set_adapter(adapter)
        model.eval()
        for row in load_rows('test'):
            path = output/f'{group}_{row["id"]}.json'
            if path.exists():
                results.append(json.loads(path.read_text(encoding='utf-8')))
                continue
            inputs = tokenizer.apply_chat_template(messages_for(row), tokenize=True, add_generation_prompt=True,
                enable_thinking=False, return_tensors='pt', return_dict=True).to('cuda')
            started = time.monotonic()
            context = model.disable_adapter() if adapter is None else nullcontext()
            with context, torch.inference_mode(), torch.autocast('cuda', dtype=torch.bfloat16):
                generated = model.generate(**inputs, max_new_tokens=512, do_sample=False, use_cache=True,
                    pad_token_id=tokenizer.eos_token_id, eos_token_id=tokenizer.eos_token_id)
            text = tokenizer.decode(generated[0, inputs['input_ids'].shape[1]:], skip_special_tokens=True)
            record = dict(group=group, id=row['id'], difficulty=row['difficulty'], input=row['input'],
                gold=row['answer'], raw_output=text, seconds=time.monotonic()-started, metrics=score_output(text, row))
            atomic_json(path, record)
            results.append(record)
            print(json.dumps(dict(group=group, id=row['id'], dsl_exact=record['metrics']['dsl_exact'])), flush=True)
    build_report(results, output)


if __name__ == '__main__':
    main()
