"""Offline checks only: imports, local data, tools, and upstream runtime setup."""
import contextlib
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'ChinaTravel'
sys.path.insert(0, str(REPO))


def main():
    os.chdir(REPO)
    report = {'python': platform.python_version(), 'executable': sys.executable, 'offline_only': True}
    packages = {
        'numpy': 'numpy', 'pandas': 'pandas', 'geopy': 'geopy', 'datasets': 'datasets',
        'openai': 'openai', 'sklearn': 'scikit-learn', 'pyarrow': 'pyarrow',
        'json_repair': 'json_repair', 'func_timeout': 'func_timeout', 'jsonschema': 'jsonschema',
    }
    for module in packages:
        importlib.import_module(module)
    report['packages'] = {dist: importlib.metadata.version(dist) for dist in packages.values()}
    from chinatravel.agent.llms import OpenAICompatibleLLM
    adapter = OpenAICompatibleLLM(model='offline-check', base_url='http://127.0.0.1:1/v1', api_key='offline-placeholder')
    assert callable(adapter.llm.chat.completions.create)
    adapter.llm.close()
    report['api_sdk_initialization'] = 'passed without HTTP requests'
    from chinatravel.data.load_datasets import load_query
    report['queries'] = {}
    for split, expected in [('smoke_local', 1), ('pilot10_local', 10), ('easy_local', 300), ('medium_local', 150), ('human_local', 154)]:
        with contextlib.redirect_stdout(io.StringIO()):
            ids, records = load_query(SimpleNamespace(splits=split, lang='zh', oracle_translation=True))
        assert len(ids) == expected and len(records) == expected
        assert all(isinstance(record['hard_logic_py'], list) for record in records.values())
        report['queries'][split] = len(ids)
    report['sandbox_verified_files'] = {}
    for lang, dirname in [('zh', 'database'), ('en', 'database_en')]:
        count = 0
        for line in (ROOT / 'datasets' / 'ChinaTravel-Sandbox' / 'manifests' / ('SHA256SUMS.' + lang)).read_text(encoding='utf-8').splitlines():
            digest, relative = line.split(maxsplit=1)
            content = (REPO / 'chinatravel' / 'environment' / dirname / relative.lstrip('*')).read_bytes()
            assert hashlib.sha256(content).hexdigest() == digest, relative
            count += 1
        report['sandbox_verified_files'][lang] = count
    from chinatravel.environment.world_env import WorldEnv
    report['environments'] = {}
    for lang in ('zh', 'en'):
        environment = WorldEnv(lang=lang)
        cities = environment.attractions.data
        assert len(cities) == 10
        report['environments'][lang] = {'cities': len(cities), 'attractions': sum(len(rows) for rows in cities.values())}
    from agent_env.runtime import AgentToolRuntime
    result = AgentToolRuntime().self_check()
    assert result['success'], result
    report['tool_runtime_self_check'] = 'passed'
    from chinatravel.agent.load_model import create_agent_runtime
    with contextlib.redirect_stdout(io.StringIO()):
        runtime = create_agent_runtime('LLMNeSy', 'environment-check-no-api', project_root_path=str(REPO), lang='zh', debug=False)
    assert runtime.agent.TIME_CUT == 300
    report['llm_nesy_initialization'] = 'passed without model calls'
    with contextlib.redirect_stdout(io.StringIO()):
        runtime = create_agent_runtime('ReAct', 'environment-check-no-api', project_root_path=str(REPO), lang='zh', debug=False)
    report['react_initialization'] = 'passed without model calls'
    report['status'] = 'passed'
    destination = ROOT / 'reproduction' / 'environment_report.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
