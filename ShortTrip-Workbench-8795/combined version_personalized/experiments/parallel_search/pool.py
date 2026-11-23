"""Collect candidates from isolated workers under an explicit stopping policy."""
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import queue
import time


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def plan_key(plan):
    fields=('people_number','start_city','target_city','itinerary')
    value={k:plan[k] for k in fields} if all(k in plan for k in fields) else plan
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def run_pool(target, bundle, *, workers, seconds, out, options=None, grace_seconds=10,
             stop_mode='first', candidate_limit=20):
    if workers < 1 or seconds <= 0 or grace_seconds < 0:
        raise ValueError('workers/seconds must be positive; grace_seconds must be nonnegative')
    if stop_mode not in ('first','target','budget') or candidate_limit < 1:
        raise ValueError('stop_mode must be first/target/budget and candidate_limit positive')
    options={**(options or {}),'stop_mode':stop_mode,'candidate_limit':candidate_limit}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    context = mp.get_context('spawn')
    stop, messages = context.Event(), context.Queue()
    started = time.monotonic()
    deadline = started + seconds
    count = min(workers, len(bundle['tasks']))
    partitions = [list(range(i, len(bundle['tasks']), count)) for i in range(count)]
    children, done, events, forced = [], {}, [], []
    winner = None
    candidates, seen = [], set()
    duplicates = late_candidates = 0
    stop_reason = None
    stopping = None
    interrupted = False
    save(out / 'INPUT.json', {'workers_requested': workers, 'workers_started': count,
         'seconds': seconds, 'partitions': partitions, 'options': options,
         'scope': 'Frozen upstream; no shared agent, cross-process cache or new search rules.'})
    try:
        for index, ids in enumerate(partitions):
            child = context.Process(target=target, args=(index, ids, bundle, stop, messages,
                                    deadline, str(out), options or {}))
            child.start()
            children.append(child)
        with (out / 'events.jsonl').open('w', encoding='utf-8', buffering=1) as journal:
            while children:
                now = time.monotonic()
                if stopping is None and (now >= deadline or len(done) == len(children)):
                    stop_reason='wall_budget' if now>=deadline else 'inventory_finished_or_error'
                    stopping = now
                    stop.set()
                if stopping is not None and now - stopping >= grace_seconds:
                    break
                try:
                    event = messages.get(timeout=.05)
                except queue.Empty:
                    if all(not p.is_alive() for p in children):
                        break
                    continue
                event['received_seconds'] = time.monotonic() - started
                events.append(event)
                journal.write(json.dumps(event, ensure_ascii=False) + '\n')
                if event['event'] == 'candidate':
                    key=plan_key(event['plan'])
                    if event['received_seconds'] > seconds:
                        late_candidates += 1
                    elif key in seen:
                        duplicates += 1
                    elif stop_reason not in ('first_candidate','candidate_target'):
                        seen.add(key)
                        candidates.append({'fingerprint':key,'worker':event['worker'],
                            'combination':event.get('combination'),
                            'generated_seconds':event['at']-started,
                            'received_seconds':event['received_seconds'],'plan':event['plan']})
                        if winner is None:
                            winner=event
                            save(out/'FIRST_PLAN.json',event['plan'])
                        if stop_mode=='first' or stop_mode=='target' and len(candidates)>=candidate_limit:
                            stop_reason='first_candidate' if stop_mode=='first' else 'candidate_target'
                            stopping=time.monotonic()
                            stop.set()
                if event['event'] == 'done':
                    done[event['worker']] = event
    except BaseException:
        interrupted = True
        raise
    finally:
        stop.set()
        stop_reason=stop_reason or ('interrupted' if interrupted else 'worker_exit_without_report')
        for index, child in enumerate(children):
            if child.is_alive():
                child.join(timeout=.1)
            if child.is_alive():
                forced.append(index)
                child.terminate()
            child.join(timeout=3)
            if child.is_alive():
                child.kill()
                child.join(timeout=3)
        outcome = ('interrupted' if interrupted else 'candidate' if winner else
                   'deadline_without_candidate' if time.monotonic() >= deadline else 'exhausted_or_error')
        summary = {'outcome': outcome, 'workers': count, 'tasks': len(bundle['tasks']),
            'stop_mode':stop_mode,'stop_reason':stop_reason,'budget_seconds':seconds,
            'candidate_target':candidate_limit if stop_mode=='target' else None,
            'system_unique_candidates':len(candidates),'cross_worker_duplicates':duplicates,
            'late_candidates_not_counted':late_candidates,
            'raw_candidate_events':sum(e['event']=='candidate' for e in events),
            'covered_combinations':sorted({e['detail']['global_combination'] for e in events
                 if e['event']=='search' and e['detail'].get('event')=='dispatch' and 'global_combination' in e['detail']}),
            'replay_missing_combinations':sorted({e['combination'] for e in events if e['event']=='replay_miss'}),
            'first_generated_seconds': winner['at'] - started if winner else None,
            'first_received_seconds': winner['received_seconds'] if winner else None,
            'winner_worker': winner['worker'] if winner else None,
            'elapsed_including_cleanup_seconds': time.monotonic() - started,
            'worker_reports': list(done.values()), 'forced_termination_workers': forced,
            'worker_exitcodes': [p.exitcode for p in children],
            'all_children_stopped': all(not p.is_alive() for p in children),
            'model_requests_started': sum(e['event'] == 'model_start' for e in events),
            'model_requests_finished': sum(e['event'] == 'model_end' for e in events),
            'model_seconds_reported': sum(e.get('seconds',0) for e in events if e['event']=='model_end'),
            'model_requests_failed': sum(e['event']=='model_end' and e.get('status')=='error_or_interrupted' for e in events),
            'api_input_tokens_reported': sum(e.get('input_tokens') or 0 for e in events if e['event']=='model_end') if (options or {}).get('provider')=='deepseek' else None,
            'api_output_tokens_reported': sum(e.get('output_tokens') or 0 for e in events if e['event']=='model_end') if (options or {}).get('provider')=='deepseek' else None,
            'model_usage_complete': (options or {}).get('provider')=='deepseek' and
                sum(e['event']=='model_start' for e in events)==sum(e['event']=='model_end' and e.get('status')=='complete' for e in events),
            'cpu_seconds_reported': sum(e.get('cpu_seconds', 0) for e in done.values()),
            'cpu_accounting_complete': len(done) == count and not forced}
        save(out / 'SUMMARY.json', summary)
        save(out / 'CANDIDATES.json', candidates)
        messages.close()
        messages.join_thread()
    return summary
