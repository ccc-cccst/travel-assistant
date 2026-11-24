"""Detached experiment supervisor with bounded recovery from complete checkpoints."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
from training_protection import atomic_json,run_lock

def run_phase(name,command,retries=0):
    for attempt in range(retries+1):
        atomic_json(ROOT/'experiment_status.json',dict(state='running',phase=name,attempt=attempt+1,pid=__import__('os').getpid(),updated_at=time.time()))
        current=list(command)
        if attempt:
            current.append('--resume')
        code=subprocess.call(current,cwd=ROOT)
        if code==0:
            return
        if code in (130,-2,-15) or attempt==retries:
            raise RuntimeError(f'{name} exited {code}')
        time.sleep(3)

def main():
    with run_lock(ROOT/'.experiment.lock'):
        try:
            if not (ROOT/'model_verified.json').exists():
                run_phase('download',[sys.executable,'-u','download_model.py'])
            for stage in ('smoke','training'):
                status_path=ROOT/('outputs/smoke/status.json' if stage=='smoke' else 'outputs/bf16_rank8/status.json')
                if status_path.exists() and json.loads(status_path.read_text()).get('state')=='completed':
                    continue
                command=[sys.executable,'-u','train.py']
                if stage=='smoke':
                    command.append('--smoke')
                else:
                    command.append('--resume')
                run_phase(stage,command,retries=0 if stage=='smoke' else 2)
            run_phase('evaluation',[sys.executable,'-u','evaluate.py'])
            atomic_json(ROOT/'experiment_status.json',dict(state='completed',updated_at=time.time()))
        except BaseException as exc:
            atomic_json(ROOT/'experiment_status.json',dict(state='failed',error=str(exc),updated_at=time.time()))
            raise

if __name__=='__main__':
    main()
