"""Reuse existing combination/day search inside each independent process."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'experiments/c_only')]


class SearchStopped(BaseException):
    pass


class ReplayMiss(Exception):
    pass


def context_key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class RecordedModel:
    """Only replay exact recorded day contexts. Unseen contexts are explicit errors."""
    name = 'recorded-deepseek-flash'

    def __init__(self, records):
        self.input_token_count = self.output_token_count = self.input_token_maxx = 0
        self.records = {}
        for record in records:
            if record.get('event') == 'model_sets':
                key = context_key(record['context'])
                # A fixed first response makes both concurrency settings reproducible.
                self.records.setdefault(key, {'sets': [{'attractions': ids} for ids in record['proposals']],
                                              'restaurants': record.get('restaurants', [])})

    def _send_request(self, messages, request_args):
        context = json.loads(messages[-1]['content'].rsplit('\n', 1)[1])
        key = context_key(context)
        if key not in self.records:
            raise ReplayMiss('No exact recorded model context; no fabricated response.')
        return {'content': json.dumps(self.records[key], ensure_ascii=False)}

    def __call__(self, messages, **_kwargs):
        return self._send_request(messages, {'max_tokens': 512, 'temperature': 0})['content']


def configure(data):
    from chinatravel.environment.sandbox.__main__ import configuration
    os.environ.update(configuration(Path(data)))
    os.environ['CHINATRAVEL_TRANSLATION_MODE'] = 'chinatravel'


def make_model(provider, records):
    if provider == 'recorded':
        return RecordedModel(records)
    from chinatravel.agent.llms import OpenAICompatibleLLM
    model = OpenAICompatibleLLM('deepseek-flash', name='deepseek-flash',
        base_url='https://api.deepseek.com', api_key=os.environ['DEEPSEEK_API_KEY'],
        max_tokens=4096, token_limit_arg='max_tokens', wire_api='chat',
        default_request_args={'temperature': 0, 'extra_body': {'thinking': {'type': 'disabled'}}})
    model._client_kwargs.update(timeout=8, max_retries=0)
    return model


def make_agent(model, out):
    import experiment_agent
    from chinatravel.environment.world_env import WorldEnv
    from experiments.timing_probe.combo_schedule import combination_agent
    cls = combination_agent(experiment_agent.ExperimentAgent, lambda _agent: None)
    return cls(group='C', candidate_limit=1, env=WorldEnv(lang='zh'), backbone_llm=model,
               cache_dir=str(out), debug=False, lang='zh')


def pack(agent, tasks, data):
    from chinatravel.agent.utils import NpEncoder
    result = {'query': copy.deepcopy(agent.query),
        'memory': {key: value.to_dict(orient='split') for key, value in agent.memory.items()},
        'required_budget': agent.required_budget,
        'local_modes': list(agent.innercity_transports_ranking_from_query),
        'tasks': [{**task, 'root': {key: dict(value) for key, value in task['root'].items()}}
                  for task in tasks], 'data': str(Path(data).resolve())}
    return json.loads(json.dumps(result, cls=NpEncoder))


def hydrate(agent, bundle):
    import pandas as pd
    agent.query = copy.deepcopy(bundle['query'])
    agent.memory = {key: pd.DataFrame(**value) for key, value in bundle['memory'].items()}
    agent.required_budget = bundle['required_budget']
    agent.innercity_transports_ranking_from_query = list(bundle['local_modes'])
    agent.time_before_search = time.time()
    agent.soft_started = time.monotonic()
    agent.prune_counts, agent.prune_examples = {}, {}
    agent.least_plan_logical_pass = -1
    agent.least_plan_logic = None
    agent.llm_inference_time_count = 0
    for field in ('llm_rec_format_error', 'llm_rec_count', 'search_nodes', 'backtrack_count',
                  'constraints_validation_count', 'commonsense_pass_count', 'logical_pass_count', 'all_constraints_pass'):
        setattr(agent, field, 0)


def worker(index, ids, bundle, stop, messages, deadline, out, options):
    folder = Path(out) / 'workers' / str(index)
    folder.mkdir(parents=True)
    started_cpu = time.process_time()
    agent = None
    status, error = 'exhausted', None
    def emit(event, **kwargs):
        messages.put({'event': event, 'worker': index, 'at': time.monotonic(), **kwargs})
    def check():
        if stop.is_set() or time.monotonic() >= deadline:
            raise SearchStopped()
    try:
        with (folder / 'search.log').open('w', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log):
            configure(bundle['data'])
            model = make_model(options['provider'], bundle.get('records', []))
            agent = make_agent(model, folder / 'cache')
            hydrate(agent, bundle)
            # The legacy adapter forces one candidate in its constructor. Only
            # this independent worker opts into continued candidate collection.
            agent.candidate_limit=1 if options.get('stop_mode','first')=='first' else sys.maxsize
            agent.pool.limit=agent.candidate_limit
            agent._deadline_check = check
            send = model._send_request
            def measured(messages_in, kwargs):
                check()
                args = dict(kwargs)
                args['timeout'] = min(8, deadline - time.monotonic(), args.get('timeout', 8))
                digest = hashlib.sha256(context_key([messages_in, {k:v for k,v in args.items() if k!='timeout'}]).encode()).hexdigest()
                at = time.monotonic()
                tokens_before = (model.input_token_count, model.output_token_count)
                request_status = 'complete'
                emit('model_start', request_sha256=digest, phase='day_sets')
                try:
                    return send(messages_in, args)
                except BaseException:
                    request_status = 'error_or_interrupted'
                    raise
                finally:
                    emit('model_end', request_sha256=digest, phase='day_sets', status=request_status,
                         seconds=time.monotonic()-at,
                         input_tokens=model.input_token_count-tokens_before[0] if options['provider']=='deepseek' and request_status=='complete' else None,
                         output_tokens=model.output_token_count-tokens_before[1] if options['provider']=='deepseek' and request_status=='complete' else None)
            model._send_request = measured
            transport = type(agent.env.transportation)
            goto = transport.goto
            def guarded_goto(self, *args, **kwargs):
                check()
                return goto(self, *args, **kwargs)
            transport.goto = guarded_goto
            active_combination=[None]
            def candidate(pool):
                check()
                emit('candidate', plan=pool.items[-1]['plan'],combination=active_combination[0])
            agent.on_candidate = candidate
            import pandas as pd
            from experiments.timing_probe.combo_schedule import dispatch
            from chinatravel.soft_constraints.agent import CandidateCollectionComplete
            tasks = [{**bundle['tasks'][i], 'combination_id':i,
                      'root': {key: pd.Series(value) for key,value in bundle['tasks'][i]['root'].items()}}
                     for i in ids]
            def trials(task):
                try:
                    yield from agent._trials(task)
                except ReplayMiss:
                    # Missing recorded material must not abort unrelated roots
                    # or turn into fabricated model output / proven infeasibility.
                    emit('replay_miss',combination=task['combination_id'])
            def search_event(event):
                detail=dict(event)
                if 'combination' in detail:
                    detail['global_combination']=ids[detail['combination']]
                    if detail['event']=='dispatch':
                        active_combination[0]=detail['global_combination']
                emit('search',detail=detail)
            emit('ready', task_ids=ids)
            try:
                dispatch(iter(tasks), trials, check, search_event)
            except CandidateCollectionComplete:
                status = 'candidate'
            from .pool import save
            save(folder / 'day_sets.json', agent.day_events)
    except SearchStopped:
        status = 'stopped'
    except Exception as exc:
        status, error = 'error', {'type': type(exc).__name__}
    finally:
        emit('done', status=status, error=error, cpu_seconds=time.process_time()-started_cpu,
             search_nodes=getattr(agent, 'search_nodes', 0))
