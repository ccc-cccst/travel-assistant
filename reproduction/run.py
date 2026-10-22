"""Run upstream ChinaTravel with an explicit API model and recorded settings."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import uuid
from urllib.parse import urlsplit
from urllib.request import getproxies

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'ChinaTravel'
sys.path.insert(0, str(REPO))


def request_profile(model, base_url):
    if urlsplit(base_url).hostname == 'ark.cn-beijing.volces.com' and model.startswith('doubao-seed-'):
        return {
            'temperature': 0,
            'extra_body': {'thinking': {'type': 'disabled'}},
        }
    if urlsplit(base_url).hostname == 'api.deepseek.com' and model in {'deepseek-v4-pro', 'deepseek-flash'}:
        return {
            'temperature': 0, 'top_p': 1.0,
            'extra_body': {'thinking': {'type': 'disabled'}},
        }
    return {'temperature': 0, 'top_p': 0.00000001}


def configure_system_proxy():
    # httpx reads proxy environment variables but not the Windows proxy registry.
    configured = []
    for scheme, proxy in getproxies().items():
        if scheme in {'http', 'https', 'all'} and proxy:
            name = scheme.upper() + '_PROXY'
            if not os.environ.get(name) and not os.environ.get(name.lower()):
                os.environ[name] = proxy
                configured.append(scheme)
    return configured


def compatible_request_args(model, base_url, request_args):
    adjusted = dict(request_args)
    if urlsplit(base_url).hostname == 'ark.cn-beijing.volces.com' and model.startswith('doubao-seed-'):
        # Seed models with thinking capability reject stop, even with thinking off.
        # Upstream still applies its one-line post-processing to the response.
        adjusted.pop('stop', None)
    return adjusted


def main():
    # Inline Python sessions on Windows can inherit GBK despite later env changes.
    # Reconfigure the underlying streams before upstream installs its log wrappers.
    for stream in (sys.__stdout__, sys.__stderr__):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, help='Exact model ID from your provider; no implicit model substitution')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model-version', required=True, help='Provider-declared checkpoint/version for the experiment record')
    parser.add_argument('--agent', choices=['LLMNeSy', 'ReAct', 'ReAct0', 'Act', 'LLM-modulo'], default='LLMNeSy')
    parser.add_argument('--split', choices=['smoke_local', 'pilot10_local', 'preview5_local', 'patterns9_local', 'patterns14_local', 'easy_local', 'medium_local', 'human_local'], default='smoke_local')
    parser.add_argument('--index', help='Run one UID from the selected split; generation only')
    parser.add_argument('--oracle', action='store_true')
    parser.add_argument('--evaluate', action='store_true', help='Evaluate existing outputs without calling a model')
    parser.add_argument('--dry-run', action='store_true', help='Show settings without API calls')
    args = parser.parse_args()
    if args.index and args.evaluate:
        parser.error('--index is for generation; evaluation covers the selected split')
    from chinatravel.agent import llms
    from chinatravel.agent.load_model import build_method_name

    # Keep different providers, checkpoint declarations and request settings apart.
    request_args = request_profile(args.model, args.base_url)
    compatibility = {'omit_stop': urlsplit(args.base_url).hostname == 'ark.cn-beijing.volces.com' and args.model.startswith('doubao-seed-')}
    signature = hashlib.sha256(json.dumps([args.model, args.base_url, args.model_version, request_args, compatibility], sort_keys=True).encode()).hexdigest()[:12]
    run_label = 'repro-' + signature
    llms.MODEL_ALIASES[run_label] = llms.ModelAlias(
        name=run_label, model=args.model, base_url=args.base_url,
        default_request_args=request_args,
    )
    method = build_method_name(args.agent, run_label, oracle_translation=args.oracle, refine_steps=10)
    if args.agent == 'LLM-modulo' and not args.oracle:
        parser.error('LLM-modulo requires --oracle in the upstream implementation')
    settings = {
        'model_id': args.model, 'provider_declared_version': args.model_version,
        'base_url': args.base_url, 'agent': args.agent, 'split': args.split,
        'lang': 'zh', 'oracle_translation': args.oracle, 'query_uid': args.index,
        'request_args': request_args, 'max_tokens': 4096,
        'provider_compatibility': compatibility,
        'method': method, 'action': 'evaluate' if args.evaluate else 'generate',
        'code_commit': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
        'tracked_diff_sha256': hashlib.sha256(subprocess.check_output(['git', '-C', str(REPO), 'diff', 'HEAD'])).hexdigest(),
    }
    if args.dry_run:
        print(json.dumps(settings, ensure_ascii=False, indent=2))
        return
    if not args.evaluate:
        if not os.environ.get('CHINATRAVEL_OPENAI_API_KEY') and not os.environ.get('OPENAI_API_KEY'):
            parser.error('No API key. Use Start-Reproduction.ps1 to enter it privately on this computer.')
        if args.model.lower() in {'deepseek', 'deepseek-chat', 'deepseek-reasoner'}:
            parser.error('This is a moving/legacy alias. Select an explicit provider model ID and record its checkpoint first.')
    os.environ['CHINATRAVEL_OPENAI_BASE_URL'] = args.base_url
    os.environ['CHINATRAVEL_OPENAI_WIRE_API'] = 'chat'
    os.environ['CHINATRAVEL_OPENAI_RAISE_ERRORS'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    configure_system_proxy()
    os.chdir(REPO)
    if args.evaluate:
        target = 'eval_exp.py'
        argv = [target, '--splits', args.split, '--method', method, '--lang', 'zh']
    else:
        target = 'run_exp.py'
        argv = [target, '--splits', args.split, '--agent', args.agent, '--llm', run_label, '--lang', 'zh', '--skip', '1']
        if args.oracle:
            argv.append('--oracle_translation')
        if args.index:
            argv.extend(['--index', args.index])
    record = {**settings, 'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'argv': argv}
    records = ROOT / 'reproduction' / 'runs'
    records.mkdir(exist_ok=True)
    # Parallel processes can observe identical timestamps on Windows/sandbox clocks.
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:12]
    (records / (stamp + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    original_record_usage = llms.OpenAICompatibleLLM._record_usage
    original_send_request = llms.OpenAICompatibleLLM._send_request

    def send_compatible_request(adapter, messages, provider_request_args):
        adjusted = compatible_request_args(adapter.model, args.base_url, provider_request_args)
        return original_send_request(adapter, messages, adjusted)

    llms.OpenAICompatibleLLM._send_request = send_compatible_request

    def record_response_metadata(adapter, completion):
        original_record_usage(adapter, completion)
        usage = getattr(completion, 'usage', None)
        metadata = {
            'recorded_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'query_uid': args.index,
            'response_model': getattr(completion, 'model', None),
            'system_fingerprint': getattr(completion, 'system_fingerprint', None),
            'prompt_tokens': getattr(usage, 'prompt_tokens', None),
            'completion_tokens': getattr(usage, 'completion_tokens', None),
            'prompt_cache_hit_tokens': getattr(usage, 'prompt_cache_hit_tokens', None),
            'prompt_cache_miss_tokens': getattr(usage, 'prompt_cache_miss_tokens', None),
            'finish_reasons': [getattr(choice, 'finish_reason', None) for choice in getattr(completion, 'choices', [])],
        }
        with (records / (stamp + '.responses.jsonl')).open('a', encoding='utf-8') as output:
            output.write(json.dumps(metadata, ensure_ascii=False) + '\n')

    llms.OpenAICompatibleLLM._record_usage = record_response_metadata
    print('Method:', method, '| Split:', args.split, '| Action:', settings['action'], flush=True)
    sys.argv = argv
    runpy.run_path(str(REPO / target), run_name='__main__')


if __name__ == '__main__':
    main()
