"""Build a local, self-contained view of actual Flash preview outputs."""
import csv
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'reproduction' / 'flash_preview_20261017'
REPO = ROOT / 'ChinaTravel'
METHOD = 'LLMNeSy_repro-75be0df8e14c'


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def metric_rows(name):
    path = REPO / 'eval_res' / 'splits_preview5_local' / METHOD / (name + '.csv')
    if not path.exists():
        return {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return {row.pop('data_id'): {key: value for key, value in row.items() if value not in ('', None)}
                for row in csv.DictReader(stream)}


def all_values(row, expected):
    return bool(row) and all(float(value) == expected for value in row.values())


def main():
    status = read_json(FOLDER / 'status.json')
    scores = {name: metric_rows(name) for name in ('schema', 'commonsense', 'logical_py')}
    usage = {}
    for path in sorted((ROOT / 'reproduction' / 'runs').glob('*.json')):
        settings = read_json(path)
        if settings.get('method') != METHOD or settings.get('action') != 'generate':
            continue
        uid = settings.get('query_uid')
        if uid not in {case['uid'] for case in status['cases']}:
            continue
        usage.setdefault(uid, [])
        responses = path.with_suffix('.responses.jsonl')
        if responses.exists():
            usage[uid] += [json.loads(line) for line in responses.read_text(encoding='utf-8').splitlines() if line]

    cases = []
    for item in status['cases']:
        uid = item['uid']
        plan_path = REPO / 'results' / METHOD / (uid + '.json')
        plan = read_json(plan_path, {})
        translated = read_json(REPO / 'cache' / 'translation_repro-75be0df8e14c_reflect' / (uid + '.json'), {})
        original = read_json(REPO / 'chinatravel/data/preview5_local' / (uid + '.json'), {})
        row = {name: scores[name].get(uid, {}) for name in scores}
        evaluated = item['status'] not in {'queued', 'running'} and all(row.values())
        verdict = {
            'evaluated': evaluated,
            'schema_pass': all_values(row['schema'], 1) if evaluated else None,
            'commonsense_pass': all_values(row['commonsense'], 0) if evaluated else None,
            'logical_pass': all_values(row['logical_py'], 1) if evaluated else None,
            'commonsense_failures': [key for key, value in row['commonsense'].items() if float(value) != 0] if evaluated else [],
            'logical_failures': [key for key, value in row['logical_py'].items() if float(value) != 1] if evaluated else [],
            'official_rows': row,
        }
        verdict['all_pass'] = all(verdict[key] for key in ('schema_pass', 'commonsense_pass', 'logical_pass')) if evaluated else None
        responses = usage.get(uid, [])
        counts = {key: sum(record.get(key) or 0 for record in responses) for key in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')}
        usage_is_attributable = (counts['prompt_tokens'] == plan.get('input_token_count') and
                                 counts['completion_tokens'] == plan.get('output_token_count'))
        # The run is on a Saturday (2026-10-17), which is wholly off-peak.
        estimate = (counts['prompt_cache_hit_tokens'] * 0.003 + counts['prompt_cache_miss_tokens'] * 0.15 + counts['completion_tokens'] * 0.6) / 1e6
        cost = sum(float(activity.get('cost', 0)) + sum(float(t.get('cost', 0)) for t in activity.get('transports', []))
                   for day in plan.get('itinerary', []) for activity in day.get('activities', []))
        analysis_notes = []
        if uid == 'e20241028160842228543':
            analysis_notes.append('模型把“主要使用地铁”收紧为所有市内交通类型都必须属于地铁，还把无市内交通的活动纳入检查；上游最终返回的候选满足原始参考约束，但内部搜索为更严格的生成约束耗尽预算。')
        if uid == 'h20241029143447759844':
            attraction_counts = [sum(a.get('type') == 'attraction' for a in day.get('activities', [])) for day in plan.get('itinerary', [])]
            analysis_notes.append('本条原始参考约束只标注了天数、人数和出租车数量，没有对“不特种兵、轻快自由行”的旅行节奏作可执行标注。')
            analysis_notes.append('按实际输出统计，每天景点数为 ' + str(attraction_counts) + '；第二、三天安排较密集，轻松程度仍需人工判断。官方通过率覆盖的是已标注的可执行约束。')
        per_case_usage = {**counts, 'requests': len(responses),
                          'response_models': sorted({r.get('response_model', '') for r in responses}),
                          'length_limited_responses': sum('length' in r.get('finish_reasons', []) for r in responses),
                          'off_peak_usd_estimate': round(estimate, 6),
                          'record_attribution_verified': usage_is_attributable}
        if plan and not usage_is_attributable:
            per_case_usage.update(prompt_tokens=plan.get('input_token_count'), completion_tokens=plan.get('output_token_count'),
                                  prompt_cache_hit_tokens=None, prompt_cache_miss_tokens=None, requests=None,
                                  length_limited_responses=None, off_peak_usd_estimate=None,
                                  note='Concurrent timestamp collision merged the first two response ledgers. Per-query input/output totals come from the original saved plan; request/cache/cost allocation is unknown. See aggregate_api_usage.')
        cases.append({**item, 'plan_file': str(plan_path), 'has_itinerary': bool(plan.get('itinerary')),
                      'plan': plan, 'generated_constraints': translated.get('hard_logic_py', []),
                      'analysis_notes': analysis_notes,
                      'reference_constraints_for_evaluation_only': original.get('hard_logic_py', []),
                      'validation': verdict, 'api_usage': per_case_usage,
                      'sandbox_cost_cny': round(cost, 2) if plan.get('itinerary') else None})

    all_responses = [response for responses in usage.values() for response in responses]
    aggregate_usage = {key: sum(response.get(key) or 0 for response in all_responses)
                       for key in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')}
    aggregate_usage['requests'] = len(all_responses)
    aggregate_usage['off_peak_usd_estimate'] = round((aggregate_usage['prompt_cache_hit_tokens'] * 0.003 +
                                                    aggregate_usage['prompt_cache_miss_tokens'] * 0.15 +
                                                    aggregate_usage['completion_tokens'] * 0.6) / 1e6, 6)
    aggregate_usage['saved_plan_token_totals_match_ledger'] = all(
        sum(case['plan'].get(plan_key, 0) for case in cases) == aggregate_usage[api_key]
        for plan_key, api_key in [('input_token_count', 'prompt_tokens'), ('output_token_count', 'completion_tokens')])
    aggregate_usage['recording_note'] = 'First two queries share a response ledger after a timestamp collision. All responses were retained and aggregate token totals match the original plans. Individual cache/request allocation for those two is not asserted. Future record names include UUIDs and each response records its query UID.'
    summary = {'model': status['model'], 'provider_declared_version': status['provider_declared_version'],
               'date_shanghai': '2026-10-17', 'method': METHOD, 'oracle_translation': False,
               'sample_count': len(cases), 'itineraries_generated': sum(c['has_itinerary'] for c in cases),
               'all_pass_count': sum(c['validation']['all_pass'] is True for c in cases),
               'per_query_wall_timeout_seconds': status['per_query_wall_timeout_seconds'],
               'cost_note': 'Estimated USD at official weekend off-peak rates; actual billing in provider console. Includes recorded successful model responses, excludes connectivity checks.',
               'pricing_source': 'https://api-docs.deepseek.com/quick_start/pricing/',
               'selection': read_json(FOLDER / 'selection.json'), 'aggregate_api_usage': aggregate_usage, 'cases': cases}
    (FOLDER / 'outputs.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    text_lines = ['ChinaTravel / DeepSeek-V4.1-Flash / 普通 NeSy',
                  '日期：2026-10-17；固定五条预览；未启用 Oracle。',
                  f'已生成 {summary["itineraries_generated"]}/5 条行程，官方评测全部通过 {summary["all_pass_count"]}/5 条。',
                  '样本不是随机抽样；行程时间和价格来自静态研究沙盒。', '']
    for i, case in enumerate(cases, 1):
        value = case['validation']['all_pass']
        verdict_text = '全部通过' if value is True else ('未全部通过' if value is False else '待评测')
        text_lines += [f'{i}. {case["source_split"]} / {case["uid"]}', '需求：' + case['query'],
                       f'状态：{case["status"]}；官方评测：{verdict_text}；总耗时：{case.get("wall_seconds", "运行中")} 秒；沙盒总费用：{case["sandbox_cost_cny"]} 元。']
        if case['plan'].get('time_out_flag'):
            text_lines.append('搜索说明：上游搜索预算耗尽后返回已保存的候选行程。以上通过状态依据数据集原始约束评测。')
        text_lines += ['观察：' + note for note in case['analysis_notes']]
        for day in case['plan'].get('itinerary', []):
            text_lines.append(f'  第 {day.get("day", "")} 天：')
            for activity in day.get('activities', []):
                label = activity.get('position') or (activity.get('start', '') + ' → ' + activity.get('end', ''))
                text_lines.append(f'    {activity.get("start_time", "")}–{activity.get("end_time", "")}  {label}  活动费 {activity.get("cost", "")} 元')
        failures = case['validation']['commonsense_failures'] + case['validation']['logical_failures']
        if failures:
            text_lines.append('未通过项：' + '；'.join(failures))
        text_lines.append('')
    (FOLDER / 'outputs.txt').write_text('\n'.join(text_lines) + '\n', encoding='utf-8')

    def esc(value):
        return html.escape(str(value))

    def badge(value):
        return '<span class="badge good">通过</span>' if value is True else ('<span class="badge bad">未通过</span>' if value is False else '<span class="badge">待评测</span>')

    def raw_details(title, value):
        return '<details><summary>' + esc(title) + '</summary><pre>' + esc(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>'

    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>ChinaTravel · DeepSeek Flash 五条真实输出</title><style>',
             'body{margin:0;background:#f4f6fa;color:#182333;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1100px;margin:auto;padding:36px 22px}h1{font-size:30px;line-height:1.25}h2{font-size:23px}h3{font-size:18px}p{margin:12px 0}.muted{color:#526175}.hero,article{background:white;border:1px solid #dbe2ec;border-radius:14px;padding:24px;margin-bottom:22px}nav{display:flex;flex-wrap:wrap;gap:10px}a{color:#1f56ad}nav a{padding:8px 13px;background:#eaf0fb;border-radius:7px;text-decoration:none}.stats{display:flex;gap:25px;flex-wrap:wrap;margin:20px 0}.stats strong{font-size:28px;display:block}.query{border-left:4px solid #3268be;padding:12px 18px;background:#f0f5fc}.badge{font-size:14px;padding:3px 9px;border-radius:20px;background:#e8ecf3;white-space:nowrap}.good{background:#d9f2e5;color:#12613e}.bad{background:#ffe3df;color:#9f2c24}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid #e1e6ef;vertical-align:top}th{background:#f4f7fb}.tablewrap{overflow:auto}pre{white-space:pre-wrap;word-break:break-word;background:#101e31;color:#e2eaf5;padding:18px;border-radius:8px;font-size:13px}details{border-top:1px solid #e0e6ef;margin-top:14px;padding-top:12px}summary{cursor:pointer;color:#23599e;font-weight:600}.facts{display:flex;gap:18px;flex-wrap:wrap;color:#526175;font-size:14px}.fail{color:#962a23}article{scroll-margin-top:15px}</style><main>',
             '<div class="hero"><div class="muted">2026-10-17 · 固定样本预览 · 非 Oracle</div><h1>DeepSeek Flash 的五条旅行规划输出</h1>',
             '<p>模型 ID：<code>deepseek-flash</code>；官方当前版本为 <b>DeepSeek-V4.1-Flash</b>。关闭深度思考，使用 ChinaTravel 普通 NeSy：模型翻译需求，再由模型推荐与符号搜索共同生成行程。</p>',
             f'<div class="stats"><div><strong>{len(cases)}</strong>固定样本</div><div><strong>{summary["itineraries_generated"]}</strong>已生成行程</div><div><strong>{summary["all_pass_count"]} / {len(cases)}</strong>官方评测全部通过</div></div>',
             '<p class="muted">选择规则：Easy 前三条、Medium 第一条、Human 第一条，运行前固定。每条设 900 秒预览上限；超时保留诊断记录。样本不是随机抽样，不能代表完整基准通过率。行程来自静态研究沙盒，时间与价格用于实验。</p>',
             '<nav>' + ''.join(f'<a href="#case{i}">{i} · {esc(c["source_split"].replace("_local", ""))}</a>' for i, c in enumerate(cases, 1)) + '<a href="outputs.json">完整 JSON</a></nav></div>']
    for index, case in enumerate(cases, 1):
        plan, validation = case['plan'], case['validation']
        parts += [f'<article id="case{index}"><h2>{index}. {esc(case["source_split"].replace("_local", ""))} · {esc(plan.get("start_city", ""))} → {esc(plan.get("target_city", ""))}</h2>',
                  '<div class="query">' + esc(case['query']) + '</div>',
                  f'<p>综合 {badge(validation["all_pass"])}　格式 {badge(validation["schema_pass"])}　常识 {badge(validation["commonsense_pass"])}　原始需求约束 {badge(validation["logical_pass"])}</p>',
                  f'<div class="facts"><span>运行状态：{esc(case["status"])}</span><span>总耗时：{esc(case.get("wall_seconds", "运行中"))} 秒</span><span>搜索：{esc(round(plan.get("search_time_sec", 0), 2))} 秒</span><span>沙盒费用：{esc(case["sandbox_cost_cny"])} 元</span><span>API 响应：{esc(case["api_usage"]["requests"] if case["api_usage"]["requests"] is not None else "见合并用量记录")}</span></div>']
        if not case['has_itinerary']:
            parts.append('<p class="fail">尚无完成的行程。请查看运行状态、生成约束和日志，不能将其算作成功输出。</p>')
        if plan.get('time_out_flag'):
            parts.append('<p class="muted">搜索预算耗尽后，上游程序返回已保存的候选行程。综合通过状态来自官方原始约束评测，与模型生成约束的内部搜索状态分别记录。</p>')
        for note in case['analysis_notes']:
            parts.append('<p class="muted">观察：' + esc(note) + '</p>')
        for day in plan.get('itinerary', []):
            parts.append(f'<h3>第 {esc(day.get("day", ""))} 天</h3><div class="tablewrap"><table><thead><tr><th>时间</th><th>活动</th><th>活动费</th><th>市内交通</th></tr></thead><tbody>')
            for activity in day.get('activities', []):
                label = activity.get('position') or (activity.get('start', '') + ' → ' + activity.get('end', ''))
                transport_id = activity.get('TrainID', activity.get('FlightID', ''))
                if transport_id:
                    label += ' · ' + transport_id
                modes = {'walk': '步行', 'metro': '地铁', 'taxi': '出租车'}
                routes = '；'.join(modes.get(t.get('mode'), str(t.get('mode', ''))) + ' ' + str(t.get('start', '')) + '→' + str(t.get('end', '')) for t in activity.get('transports', []))
                parts.append(f'<tr><td>{esc(activity.get("start_time", ""))}–{esc(activity.get("end_time", ""))}</td><td>{esc(label)}</td><td>{esc(activity.get("cost", ""))} 元</td><td>{esc(routes or "—")}</td></tr>')
            parts.append('</tbody></table></div>')
        failures = validation['commonsense_failures'] + validation['logical_failures']
        if failures:
            parts.append('<p class="fail">未通过项：' + esc('；'.join(failures)) + '</p>')
        parts += [raw_details('模型生成的 DSL 约束', case['generated_constraints']),
                  raw_details('模型与搜索程序的完整行程 JSON', plan),
                  raw_details('官方逐项评测', validation),
                  raw_details('原始参考约束（仅用于评测，未提供给模型）', case['reference_constraints_for_evaluation_only']),
                  f'<p><a href="{esc(case["uid"])}.log">查看本条完整运行日志</a> · <code>{esc(case["uid"])}</code></p></article>']
    parts.append('<p class="muted">用量记录说明：前两条并发请求发生时间戳重名，响应记录合并保留；单条输入/输出 Token 来自原始行程计数，缓存与请求次数仅报告可核对的汇总值。总 Token 已与五条原始行程一致性核对。后续记录已增加 UUID 和 UID 防止重名。</p>')
    parts.append('<p class="muted">输出由实际 API 调用与官方代码生成；本报告只做展示，不修正行程、生成约束或评测结果。数据与源码为本地已固定的维护版。</p></main></html>')
    (FOLDER / 'report.html').write_text(''.join(parts), encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ('sample_count', 'itineraries_generated', 'all_pass_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
