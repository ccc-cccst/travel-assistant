"""BF16 LoRA supervised training with answer-only loss and complete checkpoints."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parent
os.environ.update(HF_HOME=str(ROOT/'cache/huggingface'),TOKENIZERS_PARALLELISM='false',
    TORCHINDUCTOR_CACHE_DIR=str(ROOT/'cache/torchinductor'),TRITON_CACHE_DIR=str(ROOT/'cache/triton'))
from training_protection import atomic_json,complete_checkpoints,ProtectedCheckpointMixin,run_lock
from task_data import load_rows,tokenize_row
from model_runtime import use_reference_attention

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    config=json.loads((ROOT/'config.json').read_text())
    output=ROOT/('outputs/smoke' if args.smoke else config['output_dir'])
    output.mkdir(parents=True,exist_ok=True)
    status=dict(state='loading',pid=os.getpid(),smoke=args.smoke,started_at=time.time(),step=0)
    started=time.monotonic()
    def save_status():
        status['elapsed_seconds']=time.monotonic()-started
        atomic_json(output/'status.json',status,required=False)
    save_status()
    try:
        import torch
        import torch.nn.functional as functional
        import transformers
        import peft
        from transformers import (AutoTokenizer,Qwen3_5ForCausalLM,TrainingArguments,Trainer,TrainerCallback,
            DataCollatorForSeq2Seq,EarlyStoppingCallback,set_seed)
        from peft import LoraConfig,get_peft_model
        use_reference_attention()
        assert torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        set_seed(config['seed'])
        tokenizer=AutoTokenizer.from_pretrained(ROOT/config['model_dir'],local_files_only=True)
        tokenizer.pad_token=tokenizer.eos_token
        train_rows=load_rows('train')
        validation_rows=load_rows('validation')
        train_data=[tokenize_row(tokenizer,row,config['max_length']) for row in train_rows]
        validation_data=[tokenize_row(tokenizer,row,config['max_length']) for row in validation_rows]
        if args.smoke:
            train_data=sorted(train_data,key=lambda item:len(item['input_ids']),reverse=True)[:config['smoke_steps']]
        status.update(train_records=len(train_data),validation_records=len(validation_data),
            max_tokens=max(len(row['input_ids']) for row in train_data+validation_data),gpu=torch.cuda.get_device_name(),
            versions=dict(torch=torch.__version__,transformers=transformers.__version__,peft=peft.__version__))
        save_status()
        model,loading=Qwen3_5ForCausalLM.from_pretrained(ROOT/config['model_dir'],local_files_only=True,
            dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa',output_loading_info=True)
        if loading.get('missing_keys'):
            raise ValueError('Missing language-model weights '+str(loading['missing_keys']))
        assert not getattr(model,'is_loaded_in_4bit',False)
        model.config.use_cache=False
        targets=[name for name,module in model.named_modules() if '.layers.' in name and isinstance(module,torch.nn.Linear)]
        model=get_peft_model(model,LoraConfig(r=config['lora_r'],lora_alpha=config['lora_alpha'],
            lora_dropout=config['lora_dropout'],bias='none',task_type='CAUSAL_LM',target_modules=targets))
        trainable=[(name,param) for name,param in model.named_parameters() if param.requires_grad]
        assert trainable and all('lora_' in name for name,_ in trainable)
        frozen_dtypes={str(param.dtype) for name,param in model.named_parameters() if not param.requires_grad and param.ndim>=2}
        assert frozen_dtypes=={'torch.bfloat16'}, frozen_dtypes
        status.update(trainable_parameters=sum(param.numel() for _,param in trainable),
            frozen_weight_dtypes=sorted(frozen_dtypes),target_modules=targets,state='training')
        save_status()
        model.print_trainable_parameters()
        class AnswerTrainer(ProtectedCheckpointMixin,Trainer):
            def compute_loss(self,model,inputs,return_outputs=False,num_items_in_batch=None):
                labels=inputs['labels']
                indices=torch.where((labels!=-100).any(dim=0))[0]
                first,last=int(indices[0]),int(indices[-1])+1
                positions=torch.arange(first-1,last-1,device=labels.device)
                features={key:value for key,value in inputs.items() if key!='labels'}
                outputs=model(**features,logits_to_keep=positions,use_cache=False)
                loss=functional.cross_entropy(outputs.logits.float().reshape(-1,outputs.logits.shape[-1]),labels[:,first:last].reshape(-1),ignore_index=-100)
                return (loss,outputs) if return_outputs else loss
        class Progress(TrainerCallback):
            def on_train_begin(self,args,state,control,**kwargs):
                torch.cuda.reset_peak_memory_stats()
                status.update(state='training',step=state.global_step,total_steps=state.max_steps)
                save_status()
            def on_log(self,args,state,control,logs=None,**kwargs):
                if logs and any(isinstance(value,float) and not math.isfinite(value) for value in logs.values()):
                    raise ValueError('Non-finite training metric')
                status.update(step=state.global_step,epoch=state.epoch,last_metrics=logs,
                    peak_allocated_mib=round(torch.cuda.max_memory_allocated()/2**20,1),
                    peak_reserved_mib=round(torch.cuda.max_memory_reserved()/2**20,1))
                save_status()
            def on_save(self,args,state,control,**kwargs):
                status.update(checkpoint_step=state.global_step,latest_checkpoint=str(output/f'checkpoint-{state.global_step}'))
                save_status()
        total_steps=config['smoke_steps'] if args.smoke else math.ceil(len(train_data)/config['gradient_accumulation'])*config['epochs']
        arguments=TrainingArguments(output_dir=str(output),num_train_epochs=config['epochs'],
            max_steps=config['smoke_steps'] if args.smoke else -1,
            per_device_train_batch_size=1,per_device_eval_batch_size=1,
            gradient_accumulation_steps=1 if args.smoke else config['gradient_accumulation'],
            learning_rate=config['learning_rate'],lr_scheduler_type='cosine',warmup_steps=math.ceil(total_steps*0.05),max_grad_norm=1.0,
            bf16=True,tf32=True,gradient_checkpointing=True,gradient_checkpointing_kwargs={'use_reentrant':False},
            eval_strategy='no' if args.smoke else 'steps',eval_steps=config['checkpoint_steps'],
            save_strategy='steps',save_steps=1 if args.smoke else config['checkpoint_steps'],
            logging_steps=1,report_to=[],optim='adamw_torch',dataloader_num_workers=0,
            remove_unused_columns=False,prediction_loss_only=True,seed=config['seed'],data_seed=config['seed'],
            load_best_model_at_end=not args.smoke,metric_for_best_model='eval_loss',greater_is_better=False,
            save_total_limit=3,disable_tqdm=True)
        callbacks=[Progress()]
        if not args.smoke:
            callbacks.append(EarlyStoppingCallback(early_stopping_patience=6))
        trainer=AnswerTrainer(model=model,args=arguments,train_dataset=train_data,eval_dataset=validation_data,
            data_collator=DataCollatorForSeq2Seq(tokenizer=tokenizer,padding=True,label_pad_token_id=-100),
            processing_class=tokenizer,callbacks=callbacks)
        trainer.protected_checkpoints=True
        trainer.model_accepts_loss_kwargs=False
        identity=dict(config=config,smoke=args.smoke,data={split:hashlib.sha256((ROOT/'data'/f'{split}.jsonl').read_bytes()).hexdigest() for split in ('train','validation')})
        identity_path=output/'identity.json'
        if identity_path.exists() and json.loads(identity_path.read_text())!=identity:
            raise ValueError('Resume identity mismatch')
        atomic_json(identity_path,identity)
        checkpoints=complete_checkpoints(output) if args.resume else []
        resume=str(checkpoints[-1]) if checkpoints else None
        status['resumed_from']=resume
        save_status()
        with run_lock(output/'.worker.lock'):
            metrics=trainer.train(resume_from_checkpoint=resume).metrics
            trainer.save_model(output/'adapter')
            tokenizer.save_pretrained(output/'adapter')
        atomic_json(output/'result.json',dict(metrics=metrics,best_checkpoint=trainer.state.best_model_checkpoint,
            best_eval_loss=trainer.state.best_metric,step=trainer.state.global_step))
        status.update(state='completed',step=trainer.state.global_step,best_checkpoint=trainer.state.best_model_checkpoint,
            adapter_path=str(output/'adapter'),metrics=metrics)
        save_status()
    except BaseException as exc:
        status.update(state='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error_type=type(exc).__name__,
            error=str(exc),traceback=traceback.format_exc())
        save_status()
        raise

if __name__=='__main__':
    main()
