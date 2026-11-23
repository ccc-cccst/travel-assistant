"""Paired held-out evaluation, retaining every raw output for semantic review."""
from contextlib import nullcontext
import html
import json
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parent
os.environ.update(HF_HOME=str(ROOT/'cache/huggingface'),TOKENIZERS_PARALLELISM='false',TRITON_CACHE_DIR=str(ROOT/'cache/triton'))
from task_data import load_rows,messages_for,canonical_constraints
from training_protection import atomic_json
from model_runtime import use_reference_attention

def metrics_for(output,row):
    try:
        text=output.strip()
        if text.startswith('```'):
            text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
        answer=json.loads(text)
        if not isinstance(answer,dict) or set(answer)!={'soft_logic','soft_logic_evidence','unmapped_requirements'}:
            raise ValueError('Output schema mismatch')
        if not all(isinstance(answer[field],list) for field in answer):
            raise ValueError('Output fields must be lists')
        predicted=canonical_constraints(answer['soft_logic'])
        expected=canonical_constraints(row['answer']['soft_logic'])
        evidence=answer['soft_logic_evidence']
        source_valid=all(isinstance(item,dict) and isinstance(item.get('source'),str) and item['source'] and item['source'] in row['input'] and item.get('constraint') in answer['soft_logic'] for item in evidence)
        evidence_complete=source_valid and {item['constraint'] for item in evidence}==set(answer['soft_logic'])
        return dict(json_valid=True,dsl_exact=predicted==expected,true_positive=len(predicted&expected),
            predicted=len(predicted),expected=len(expected),evidence_complete=evidence_complete,
            gold_unmapped=bool(row['answer']['unmapped_requirements']),predicted_unmapped=bool(answer['unmapped_requirements']),
            unmapped_presence_correct=bool(row['answer']['unmapped_requirements'])==bool(answer['unmapped_requirements']),answer=answer)
    except (ValueError,TypeError,KeyError,SyntaxError) as exc:
        return dict(json_valid=False,dsl_exact=False,evidence_complete=False,error=str(exc),true_positive=0,
            predicted=0,expected=len(canonical_constraints(row['answer']['soft_logic'])))

def main():
    import torch
    from transformers import AutoTokenizer,Qwen3_5ForCausalLM
    from peft import PeftModel
    use_reference_attention()
    config=json.loads((ROOT/'config.json').read_text())
    output=ROOT/'evaluation'
    output.mkdir(exist_ok=True)
    tokenizer=AutoTokenizer.from_pretrained(ROOT/config['model_dir'],local_files_only=True)
    base=Qwen3_5ForCausalLM.from_pretrained(ROOT/config['model_dir'],local_files_only=True,dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa')
    model=PeftModel.from_pretrained(base,ROOT/config['output_dir']/'adapter')
    model.eval()
    rows=load_rows('test')
    results=[]
    for group in ('base_bf16','lora_bf16'):
        for row in rows:
            path=output/f'{group}_{row["id"]}.json'
            if path.exists():
                results.append(json.loads(path.read_text(encoding='utf-8')))
                continue
            inputs=tokenizer.apply_chat_template(messages_for(row),tokenize=True,add_generation_prompt=True,
                enable_thinking=False,return_tensors='pt',return_dict=True).to('cuda')
            started=time.monotonic()
            context=model.disable_adapter() if group=='base_bf16' else nullcontext()
            with context,torch.inference_mode():
                generated=model.generate(**inputs,max_new_tokens=768,do_sample=False,use_cache=True,
                    pad_token_id=tokenizer.eos_token_id,eos_token_id=tokenizer.eos_token_id)
            text=tokenizer.decode(generated[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True)
            record=dict(group=group,id=row['id'],difficulty=row['difficulty'],input=row['input'],gold=row['answer'],
                raw_output=text,seconds=time.monotonic()-started,metrics=metrics_for(text,row))
            atomic_json(path,record)
            results.append(record)
            print(json.dumps(dict(group=group,id=row['id'],seconds=record['seconds'],dsl_exact=record['metrics']['dsl_exact'])),flush=True)
    summary={}
    for group in ('base_bf16','lora_bf16'):
        group_rows=[record for record in results if record['group']==group]
        true_positive=sum(record['metrics']['true_positive'] for record in group_rows)
        predicted=sum(record['metrics']['predicted'] for record in group_rows)
        expected=sum(record['metrics']['expected'] for record in group_rows)
        summary[group]=dict(total=len(group_rows),json_valid=sum(record['metrics']['json_valid'] for record in group_rows),
            dsl_exact=sum(record['metrics']['dsl_exact'] for record in group_rows),
            evidence_complete=sum(record['metrics']['evidence_complete'] for record in group_rows),
            precision=true_positive/predicted if predicted else 0,recall=true_positive/expected if expected else 1,
            per_difficulty={difficulty:dict(total=sum(record['difficulty']==difficulty for record in group_rows),
                exact=sum(record['difficulty']==difficulty and record['metrics']['dsl_exact'] for record in group_rows)) for difficulty in ('simple','moderate','very_vague')})
    atomic_json(output/'summary.json',summary)
    cards=[]
    for row in rows:
        cards.append('<article><h2>'+html.escape(row['id'])+'</h2><p>'+html.escape(row['input'])+'</p><h3>标准答案</h3><pre>'+html.escape(json.dumps(row['answer'],ensure_ascii=False,indent=2))+'</pre>')
        for group in ('base_bf16','lora_bf16'):
            record=next(item for item in results if item['group']==group and item['id']==row['id'])
            cards.append('<h3>'+group+'</h3><pre>'+html.escape(record['raw_output'])+'</pre><p>DSL匹配 '+str(record['metrics']['dsl_exact'])+' · '+str(round(record['seconds'],1))+'秒</p>')
        cards.append('</article>')
    (output/'comparison.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>BF16 LoRA翻译对照</title><style>body{font:16px/1.7 sans-serif;max-width:1100px;margin:32px auto}article{border:1px solid #ccc;padding:24px;margin:24px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:16px}</style><h1>软约束翻译对照</h1><p>同一基础模型BF16推理，分别关闭和启用LoRA。语义正确性仍需结合逐条输出复核。</p><pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+''.join(cards)+'</html>',encoding='utf-8')

if __name__=='__main__':
    main()
