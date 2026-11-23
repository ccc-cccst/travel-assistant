"""Diagnose one real training example without applying an optimizer update."""
import argparse
import inspect
import json
import math
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parent
os.environ.update(TRITON_CACHE_DIR=str(ROOT/'cache/triton'),TORCHINDUCTOR_CACHE_DIR=str(ROOT/'cache/torchinductor'),TOKENIZERS_PARALLELISM='false')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reference',action='store_true')
    args=parser.parse_args()
    import torch
    import torch.nn.functional as F
    from transformers import AutoTokenizer,Qwen3_5ForCausalLM,set_seed
    from peft import LoraConfig,get_peft_model
    from task_data import load_rows,tokenize_row
    if args.reference:
        from transformers.models.qwen3_5 import modeling_qwen3_5
        modeling_qwen3_5.torch_chunk_gated_delta_rule=inspect.unwrap(modeling_qwen3_5.torch_chunk_gated_delta_rule)
    set_seed(20261115)
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'base_model',local_files_only=True)
    rows=[tokenize_row(tokenizer,row,1024) for row in load_rows('train')]
    row=max(rows,key=lambda item:len(item['input_ids']))
    model=Qwen3_5ForCausalLM.from_pretrained(ROOT/'base_model',local_files_only=True,dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa')
    model.config.use_cache=False
    targets=[name for name,module in model.named_modules() if '.layers.' in name and isinstance(module,torch.nn.Linear)]
    model=get_peft_model(model,LoraConfig(r=8,lora_alpha=16,lora_dropout=0.05,bias='none',task_type='CAUSAL_LM',target_modules=targets))
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
    model.train()
    inputs={key:torch.tensor([value],device='cuda') for key,value in row.items()}
    labels=inputs.pop('labels')
    selected=torch.where(labels[0]!=-100)[0]
    first,last=int(selected[0]),int(selected[-1])+1
    started=time.monotonic()
    with torch.autocast('cuda',dtype=torch.bfloat16):
        outputs=model(**inputs,logits_to_keep=torch.arange(first-1,last-1,device='cuda'),use_cache=False)
        loss=F.cross_entropy(outputs.logits.float().reshape(-1,outputs.logits.shape[-1]),labels[:,first:last].reshape(-1))
    print(json.dumps(dict(reference=args.reference,loss=loss.item(),logits_finite=torch.isfinite(outputs.logits).all().item(),forward_seconds=time.monotonic()-started)),flush=True)
    loss.backward()
    grads=[(name,param.grad) for name,param in model.named_parameters() if param.requires_grad and param.grad is not None]
    bad=[name for name,grad in grads if not torch.isfinite(grad).all()]
    result=dict(reference=args.reference,loss=loss.item(),nonfinite_gradient_tensors=len(bad),first_bad=bad[:12],
        norm=torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],float('inf')).item(),seconds=time.monotonic()-started,
        peak_mib=round(torch.cuda.max_memory_allocated()/2**20,1))
    for name in ('loss','norm'):
        result[name+'_finite']=math.isfinite(result[name])
        if not result[name+'_finite']:
            result[name]=None
    (ROOT/f'numerics_{"reference" if args.reference else "fla"}.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result),flush=True)

if __name__=='__main__':
    main()
