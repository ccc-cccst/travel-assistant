"""EXP016: paired live ShortTrip runs with and without both EXP015 rules."""

import argparse
import ctypes
import json
from pathlib import Path
import shutil
from statistics import median
import time
from types import SimpleNamespace

from live_report import profile, read
from live_suite import digest, measure, RUNTIME, MODEL
from rule_live_changes import build
from speed_report import details, table
from speed_study import DATASET, PILOT, write


# Interleave tiers and alternate the first arm within each pair.
CASES = (('easy_025', 'regression'), ('medium_113', 'regression'), ('human_038', 'regression'),
         ('easy_015', 'rule_trigger'), ('medium_013', 'rule_trigger'), ('human_073', 'rule_trigger'),
         ('easy_075', 'holdout'), ('medium_063', 'holdout'), ('human_013', 'holdout'))
HARNESS = ('live_profile.py', 'spans.py', 'rsi_transport.py')
SOURCE = PILOT / 'sources/shorttrip'


def load_cases():
    return {case: (DATASET / f'{case}.json', design) for case, design in CASES}


def prepare(out, seconds):
    cases = load_cases()
    out.mkdir(parents=True, exist_ok=False)
    queries = out / 'queries'
    queries.mkdir()
    for case, (path, _) in cases.items():
        shutil.copy2(path, queries / f'{case}.json')
    harness = out / 'harness'
    harness.mkdir()
    for script in HARNESS:
        shutil.copy2(Path(__file__).with_name(script), harness / script)
    patches = build(SOURCE, out / 'sources/rules')
    source_manifest = read(PILOT / 'manifest.json')
    checksums = source_manifest['data_sha256']
    for source in (SOURCE, out / 'sources/rules'):
        data_root = source / 'chinatravel/environment'
        if any(digest(data_root / relative) != checksum for relative, checksum in checksums.items()):
            raise ValueError(f'Sandbox integrity check failed for {source}')
    write(out / 'MANIFEST.json', {'cases': dict(CASES), 'seconds': seconds, 'max_tokens': 4096, 'seed': 20261112,
                                  'candidate_limit': 20, 'upstream_experiment': 'EXP013_rsi_v3',
                                  'control_source': str(SOURCE), 'source_manifest': str(PILOT / 'manifest.json'),
                                  'harness': {s: digest(harness / s) for s in HARNESS},
                                  'query_sha256': {c: digest(queries / f'{c}.json') for c in cases},
                                  'patch_sha256': {p: digest(out / 'sources/rules' / p) for p in patches},
                                  'model_sha256': read(RUNTIME / 'runtime_manifest.json')['files'][0]['sha256']})
    write(out / 'STATUS.json', {'state': 'prepared', 'current': None, 'finished': 0, 'total': 18, 'errors': []})


def legal(row):
    return row['common_audit'] == row['c_audit'] == 'pass'


def timeout(row):
    return row['stop'] in {'matched_wall_budget', 'wall_limit_reached'}


def report(out, results):
    pairs = [(case, arms['control'], arms['rules']) for case, arms in results.items() if len(arms) == 2]
    lines = ['# EXP016 两条规则的完整规划对照', '',
             f'已完成 {len(pairs)}/9 对。以同一冻结 ShortTrip C 组和同配置本地 Qwen 运行。'
             '两组沿用 EXP013 第三版的返程策略，差异为餐费下界和景点接驳前的时间窗检查。'
             '独立审核在规划计时之后。', '']
    groups = [(tier, [p for p in pairs if p[0].startswith(tier)]) for tier in ('easy', 'medium', 'human')]
    lines += table(['层次', '配对数', '审核合法 原→新', '无合法结果且截止 原→新', '所有截止 原→新', '总规划秒 原→新'], (
        (tier, len(rows), f'{sum(legal(a) for _,a,_ in rows)}→{sum(legal(b) for _,_,b in rows)}',
         f'{sum(timeout(a) and not legal(a) for _,a,_ in rows)}→{sum(timeout(b) and not legal(b) for _,_,b in rows)}',
         f'{sum(timeout(a) for _,a,_ in rows)}→{sum(timeout(b) for _,_,b in rows)}',
         f'{sum(a["seconds"] for _,a,_ in rows):.2f}→{sum(b["seconds"] for _,_,b in rows):.2f}')
        for tier, rows in [*groups, ('合计', pairs)]))
    lines += table(['需求', '原/新', '类别', '规划秒', '首份共同审核秒', '共同/C', '候选', '模型秒/次数', '路线查询',
                    'DFS 入口', '餐费前置排除', '查路前排除', '停止'], (
        (case, arm, dict(CASES)[case], row['seconds'], row['first_seconds'], f'{row["common_audit"]}/{row["c_audit"]}',
         row['candidates'], f'{row["llm"]:.2f}/{row["request_count"]}', row['route_lookups'], row['dfs_calls'],
         row['prune_counts'].get('mandatory_meal_budget_floor', 0), row['prune_counts'].get('pre_route_meal_window', 0), row['stop'])
        for case, arms in results.items() for arm, row in arms.items()))
    lines += table(['需求', '组别', '费用元', '市内交通分钟', '不同景点数', '重访', '换酒店', '节奏分'], (
        (case, arm, *(row['quality'].get(key) for key in ('total_cost', 'transport_minutes', 'unique_attractions',
                                                       'nonessential_revisits', 'hotel_switches', 'R')))
        for case, arms in results.items() for arm, row in arms.items() if 'quality' in row))
    kept = [p for p in pairs if legal(p[1]) and legal(p[2])]
    if kept:
        exact = sum(a['plan_fingerprint'] == b['plan_fingerprint'] for _, a, b in kept)
        lines.append(f'两侧均合法的 {len(kept)} 对中，{exact} 对选中行程指纹相同。')
    joint = [b['first_seconds'] - a['first_seconds'] for _, a, b in kept if a['first_seconds'] is not None and b['first_seconds'] is not None]
    if joint:
        lines.append(f'{len(joint)} 对两侧均有首份通过共同审核的方案，中位差 {median(joint):.2f} 秒。')
    write(out / 'RESULTS.json', results)
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def wait_for_batch(path, out, status):
    if path is None:
        return
    while True:
        try:
            state = read(path).get('state')
        except (OSError, ValueError):
            state = 'reporting'
        if state not in {'starting', 'running', 'reporting'}:
            break
        status.update(state='waiting_for_gpu', waiting_for=str(path))
        write(out / 'STATUS.json', status)
        time.sleep(15)
    status.pop('waiting_for', None)


def run(out, seconds, port, wait_for):
    manifest = read(out / 'MANIFEST.json')
    if manifest['seconds'] != seconds:
        raise ValueError('The requested budget differs from the prepared manifest')
    if digest(MODEL) != manifest['model_sha256']:
        raise ValueError('The local Qwen weights changed')
    for directory, hashes in (('queries', {f'{c}.json': h for c, h in manifest['query_sha256'].items()}),
                              ('harness', manifest['harness']), ('sources/rules', manifest['patch_sha256'])):
        if any(digest(out / directory / k) != h for k, h in hashes.items()):
            raise ValueError(f'Prepared files changed in {directory}')
    result_path = out / 'RESULTS.json'
    results = read(result_path) if result_path.exists() else {}
    status = {'state': 'prepared', 'current': None, 'finished': sum(map(len, results.values())), 'total': 18, 'errors': []}
    power = ctypes.windll.kernel32
    power.SetThreadExecutionState(0x80000001)
    try:
        wait_for_batch(wait_for, out, status)
        for i, (case, _) in enumerate(CASES):
            order = ('control', 'rules') if i % 2 == 0 else ('rules', 'control')
            query_path = out / 'queries' / f'{case}.json'
            for arm in order:
                if arm in results.get(case, {}):
                    continue
                status.update(state='running', current={'case': case, 'arm': arm})
                write(out / 'STATUS.json', status)
                source = SOURCE if arm == 'control' else out / 'sources/rules'
                attempt = 1
                while True:
                    run_dir = out / 'runs' / arm / f'attempt_{attempt}'
                    folder = run_dir / case / 'shorttrip'
                    if not folder.exists():
                        break
                    metrics = folder / 'profile/run.json'
                    if metrics.exists():
                        previous = read(metrics)
                        if not previous['error'] and previous['wall_seconds'] <= seconds + 25:
                            break
                    attempt += 1
                settings = SimpleNamespace(out=run_dir, port=port, seconds=seconds, max_tokens=4096,
                                           original_stops=True, preflight=True, return_batch_size=3,
                                           profile_script=out / 'harness/live_profile.py')
                if not folder.exists():
                    measure(settings, 'shorttrip', case, source, query_path)
                stats = profile(folder, read(query_path))
                if stats['error'] or stats['wall_seconds'] > seconds + 25:
                    raise RuntimeError(f'Invalid run at {folder}: {stats["error"]!r} {stats["wall_seconds"]:.1f}s')
                row = details('EXP016', arm, case, 'shorttrip', folder, read(query_path), stats)
                row.update(route_lookups=stats['stats'].get('route_lookup', {}).get('calls', 0),
                           prune_counts=stats.get('prune_counts', {}), path=str(folder))
                results.setdefault(case, {})[arm] = row
                status['finished'] += 1
                report(out, results)
                write(out / 'STATUS.json', status)
        status.update(state='completed', current=None)
    except Exception as error:
        status.update(state='failed', errors=[{'type': type(error).__name__, 'message': str(error)}])
        raise
    except BaseException:
        status['state'] = 'interrupted'
        raise
    finally:
        write(out / 'STATUS.json', status)
        power.SetThreadExecutionState(0x80000000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=360)
    parser.add_argument('--port', type=int, default=18092)
    parser.add_argument('--wait-for', type=lambda p: Path(p).resolve())
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.wait_for is not None and not args.wait_for.is_file():
        parser.error('--wait-for must point to an existing status file')
    out = args.out.resolve()
    if not args.resume:
        prepare(out, args.seconds)
    if not args.prepare_only:
        run(out, args.seconds, args.port, args.wait_for)


if __name__ == '__main__':
    main()
