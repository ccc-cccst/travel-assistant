"""Summarize actual plans, official scores, and explicitly sourced semantic audits."""
import csv
from functools import lru_cache
import html
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'ChinaTravel'
OUT = ROOT / 'reproduction/flash_patterns_20261017'
METHOD = 'LLMNeSy_repro-75be0df8e14c'
CITIES = dict(zip(['上海','北京','深圳','广州','重庆','成都','杭州','武汉','南京','苏州'],
                  ['shanghai','beijing','shenzhen','guangzhou','chongqing','chengdu','hangzhou','wuhan','nanjing','suzhou']))


def read(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return default  # A running upstream process may still be writing its plan.


def minute(value):
    h, m = str(value).split(':')[:2]
    return int(h) * 60 + int(m)


def display_number(value, unit=''):
    if value is None:
        return '未提供'
    return f'{value:g}{unit}' if isinstance(value, (int, float)) else str(value) + unit


def itinerary_rows(activity):
    """Show every saved transfer before the activity it leads to, without inventing routes."""
    esc = lambda value: html.escape(str(value))
    kinds = {'train': '火车', 'airplane': '飞机', 'attraction': '游览',
             'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐', 'accommodation': '住宿',
             'walk': '步行', 'metro': '地铁', 'taxi': '出租车'}
    entries = [(t, True) for t in activity.get('transports', [])] + [(activity, False)]
    rows, lines = [], []
    for entry, transfer in entries:
        kind = entry.get('mode') if transfer else entry.get('type')
        label = kinds.get(kind, kind or '未提供')
        start, end = entry.get('start_time'), entry.get('end_time')
        time = (start or '未提供') + '–' + (end or '未提供')
        duration = f'{(minute(end)-minute(start)) % 1440} 分钟' if start and end else '时长未提供'
        route = entry.get('position') or (entry.get('start', '未提供') + ' → ' + entry.get('end', '未提供'))
        details = []
        if kind in ('train', 'airplane'):
            key, title = ('TrainID', '车次') if kind == 'train' else ('FlightID', '航班号')
            details.append(title + '：' + str(entry.get(key) or '原始输出未提供'))
        if kind == 'metro':
            details.append('线路号及换乘：原始输出未提供')
        if entry.get('distance') is not None:
            details.append('沙盒距离 ' + display_number(entry['distance'], ' 公里'))
        if entry.get('cars') is not None:
            details.append(display_number(entry['cars'], ' 辆车'))
        if entry.get('tickets') is not None:
            details.append(display_number(entry['tickets'], ' 张票'))
        if kind == 'accommodation':
            details.append(display_number(entry.get('rooms'), ' 间房'))
            room = entry.get('room_type')
            details.append('房型：' + {'single': '单床房', 'twin': '双床房'}.get(room, str(room) if room else '未提供'))
        detail = '；'.join(details) or '—'
        cost = display_number(entry.get('cost'), ' 元')
        row_class = 'transport-row' if transfer else 'activity-row'
        rows.append(f'<tr class="{row_class}"><td>{esc(time)}<br><small>{esc(duration)}</small></td><td><span class="kind">{esc(label)}</span></td><td>{esc(route)}</td><td>{esc(detail)}</td><td>{esc(cost)}</td></tr>')
        lines.append(f'    {time}  {label}  {route}  {detail}  {cost}')
    return ''.join(rows), lines


def publish_report(content):
    (OUT / 'report.html').write_text(content, encoding='utf-8')
    # Keep the URL already open in the user's browser current as well.
    preview = ROOT / 'reproduction/flash_preview_20261017/report.html'
    def relocate(match):
        href = match.group(1)
        if href.startswith(('#', '/', 'http:', 'https:', 'file:')):
            return match.group(0)
        return 'href="../flash_patterns_20261017/' + href + '"'
    preview.write_text(re.sub(r'href="([^"]*)"', relocate, content), encoding='utf-8')


@lru_cache(maxsize=None)
def database(kind, city):
    directory = REPO / 'chinatravel/environment/database' / kind / CITIES[city]
    path = next(directory.glob('*.csv'))
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return {row['name']: row for row in csv.DictReader(stream)}


def main():
    sources = [ROOT / 'reproduction/flash_preview_20261017', OUT]
    states = [read(path / 'status.json', {'cases': []}) for path in sources]
    metrics = {}
    for name in ('schema', 'commonsense', 'logical_py'):
        path = REPO / 'eval_res/splits_patterns14_local' / METHOD / (name + '.csv')
        if path.exists():
            with path.open(encoding='utf-8-sig', newline='') as stream:
                metrics[name] = {row.pop('data_id'): {k:v for k,v in row.items() if v not in ('',None)} for row in csv.DictReader(stream)}
        else:
            metrics[name] = {}
    audits = read(OUT / 'semantic_audit.json', {'findings': []})
    cases = []
    for folder, state in zip(sources, states):
        for entry in state['cases']:
            uid = entry['uid']
            plan = read(REPO / 'results' / METHOD / (uid + '.json'), {})
            original = read(REPO / 'chinatravel/data/patterns14_local' / (uid + '.json'), {})
            translated = read(REPO / 'cache/translation_repro-75be0df8e14c_reflect' / (uid + '.json'), {})
            evaluated = entry['status'] not in ('queued', 'running') and all(uid in metrics[n] for n in metrics)
            scores = {}
            failed = {}
            for name, expected in [('schema',1),('commonsense',0),('logical_py',1)]:
                row = metrics[name].get(uid, {})
                scores[name] = bool(row) and all(float(v)==expected for v in row.values()) if evaluated else None
                failed[name] = [k for k,v in row.items() if float(v)!=expected] if evaluated else []
            scores['all_pass'] = all(scores.values()) if evaluated else None
            city = plan.get('target_city', original.get('target_city'))
            duration = []
            counts = []
            below_minimum = []
            hotels = []
            meals = []
            costs = 0
            for day in plan.get('itinerary', []):
                day_count = 0
                for activity in day.get('activities', []):
                    costs += float(activity.get('cost',0)) + sum(float(t.get('cost',0)) for t in activity.get('transports',[]))
                    kind = activity.get('type'); name = activity.get('position')
                    if kind == 'attraction':
                        day_count += 1
                        minutes = (minute(activity['end_time']) - minute(activity['start_time'])) % 1440
                        duration.append(minutes)
                        row = database('attractions', city).get(name,{})
                        minimum = float(row.get('recommendmintime') or 0)*60
                        if 0 < minutes < minimum:
                            below_minimum.append({'day':day['day'],'name':name,'actual_minutes':minutes,'sandbox_recommended_minimum_minutes':minimum})
                    elif kind == 'accommodation':
                        row = database('accommodations', city).get(name,{})
                        hotels.append({'name':name,'rooms':activity.get('rooms'),'room_type':activity.get('room_type'),'feature':row.get('featurehoteltype'),'lat':row.get('lat'),'lon':row.get('lon')})
                    elif kind in ('breakfast','lunch','dinner'):
                        row = database('restaurants',city).get(name,{})
                        meals.append({'day':day['day'],'name':name,'cuisine':row.get('cuisine')})
                counts.append(day_count)
            debug_candidate = read(OUT / 'debug_candidates' / (uid + '.json'), {})
            cases.append({**entry,'round':'previous5' if folder!=OUT else 'new9','official_scores':scores,'official_failures':failed,
                          'plan':plan,'generated_constraints':translated.get('hard_logic_py',[]),'reference_constraints':original.get('hard_logic_py',[]),
                          'debug_candidate':debug_candidate,
                          'has_plan':bool(plan.get('itinerary')),'search_timeout':bool(plan.get('time_out_flag')),
                          'attractions_per_day':counts,'attraction_stay_minutes':duration,'below_recommended_stay':below_minimum,
                          'hotels':hotels,'meals':meals,'sandbox_cost_cny':round(costs,2) if plan.get('itinerary') else None,
                          'audits':[finding for finding in audits['findings'] if uid in finding.get('case_uids',[])],
                          'log':str(folder/(uid+'.log'))})
    stats = {'attempted':len(cases),'completed_processes':sum(c['status']=='completed' for c in cases),
             'generated_plans':sum(c['has_plan'] for c in cases),'official_all_pass':sum(c['official_scores']['all_pass'] is True for c in cases),
             'external_wall_timeout_count':sum(c['status']=='wall_timeout' for c in cases),
             'process_error_count':sum(c['status'] in ('error','launcher_error') for c in cases),
             'zero_attraction_plans':sum(c['has_plan'] and sum(c['attractions_per_day'])==0 for c in cases),
             'zero_attraction_plans_passing_official_checks':sum(c['has_plan'] and sum(c['attractions_per_day'])==0 and c['official_scores']['all_pass'] is True for c in cases),
             'multiday_plans':sum(len(c['attractions_per_day'])>1 for c in cases),
             'internal_search_timeout_count':sum(c['search_timeout'] for c in cases),
             'no_first_day_attraction_multiday':sum(len(c['attractions_per_day'])>1 and c['attractions_per_day'][0]==0 for c in cases),
             'attraction_visits':sum(len(c['attraction_stay_minutes']) for c in cases),
             'ninety_minute_attraction_visits':sum(d==90 for c in cases for d in c['attraction_stay_minutes']),
             'visits_below_sandbox_recommended_minimum':sum(len(c['below_recommended_stay']) for c in cases)}
    summary = {'date_shanghai':'2026-10-17','model':'deepseek-flash','provider_declared_version':'DeepSeek-V4.1-Flash',
               'method':METHOD,'oracle':False,'stats':stats,'cases':cases,'audit_findings':audits['findings'],
               'scope':'Exploratory sample: previous convenience five plus six stratified random and three targeted Human samples; repeated observations are not population-wide estimates.',
               'sources':{'selection':str(OUT/'selection.json'),'official_evaluation':str(REPO/'eval_res/splits_patterns14_local'/METHOD),
                          'fixed_dwell_implementation':'ChinaTravel/chinatravel/agent/nesy_agent/llm_driven_rec.py:354'}}
    (OUT/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    esc = lambda value: html.escape(str(value))
    badge = lambda value: '通过' if value is True else ('未通过' if value is False else '待完成')
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChinaTravel · 重复缺点分析</title><style>',
             'body{margin:0;background:#f4f6fa;color:#192b3c;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1150px;margin:auto;padding:32px 24px}header,section,article{background:white;border:1px solid #dce3ed;border-radius:12px;padding:24px;margin-bottom:20px}h1{font-size:30px}h2{font-size:22px}p{margin:10px 0}.muted{color:#59697a}.query{padding:12px;border-left:4px solid #2b66b8;background:#edf3fc}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;padding:9px;border-bottom:1px solid #e2e7f0}th{background:#eff3f8}a{color:#225dab}pre{white-space:pre-wrap;word-break:break-word;font-size:13px;background:#142238;color:#edf2fa;border-radius:8px;padding:16px}details{margin:12px 0}summary{cursor:pointer;color:#225dab}.note{border-left:3px solid #b18020;padding-left:14px}.stats{display:flex;gap:30px;flex-wrap:wrap}.stats b{font-size:28px;display:block}article{scroll-margin-top:12px}</style><main>',
             '<style>.transport-row{background:#f0f6ff}.kind{white-space:nowrap;font-weight:600}small{color:#59697a}td:first-child{white-space:nowrap}td:last-child{white-space:nowrap}.day-cost{font-size:14px;color:#59697a}</style>',
             '<header><div class="muted">2026-10-17 · 普通 NeSy · 关闭深度思考 · 未启用 Oracle</div><h1>Flash 行程输出与缺点分析 · 14 条案例</h1>',
             f'<div class="stats"><div><b>{stats["generated_plans"]}/{len(cases)}</b>生成行程</div><div><b>{stats["official_all_pass"]}/{len(cases)}</b>原始可执行约束全部通过</div><div><b>{stats["internal_search_timeout_count"]}</b>上游搜索预算耗尽后返回</div><div><b>{stats["external_wall_timeout_count"]}</b>15分钟外部上限中断</div></div>',
             '<p class="muted">前 5 条保留；新增 Easy、Medium 各随机 3 条，Human 按饮食、时间、节奏各选 1 条。先选样再运行。新增四条并发、每条 900 秒外部上限；运行状态和中断均保留。此样本用于找重复现象，不能推断完整基准的发生率，也不能将整个 NeSy 系统的问题全部归给 Flash。</p><p><a href="analysis.json">完整分析 JSON</a> · <a href="outputs.txt">全部需求与行程文本</a> · <a href="semantic_counterexamples.json">离线语义反例（非模型输出）</a> · <a href="implementation_checks.json">酒店候选截断验证</a></p></header>']
    parts.append('<section><h2>重复现象与归因</h2>')
    for finding in audits['findings']:
        links = '、'.join(f'<a href="#case{next(i for i,c in enumerate(cases,1) if c["uid"]==uid)}">案例{next(i for i,c in enumerate(cases,1) if c["uid"]==uid)}</a>' for uid in finding.get('case_uids',[]) if any(c['uid']==uid for c in cases))
        parts += [f'<h3>{esc(finding["title"])}</h3><p>{esc(finding["explanation"])}</p><p class="muted">归因：{esc(finding["attribution"])}；证据：{links or esc(finding.get("source", "源码"))}。{esc(finding.get("qualification", ""))}</p>']
    parts.append(f'<p>已保存行程中，{stats["zero_attraction_plans"]} 条全程没有景点，其中 {stats["zero_attraction_plans_passing_official_checks"]} 条仍通过全部官方检查；多日行程有 {stats["no_first_day_attraction_multiday"]}/{stats["multiday_plans"]} 条首日没有景点。</p>')
    parts.append(f'<p>实现层统计：{stats["ninety_minute_attraction_visits"]}/{stats["attraction_visits"]} 次景点停留恰为 90 分钟；{stats["visits_below_sandbox_recommended_minimum"]} 次短于沙盒建议最短时长。推荐时长是诊断参照，未额外加入官方评分。</p></section>')
    parts.append('<section><h2>交通详情阅读说明</h2><p>每一天按原行程顺序显示全部市内接驳和活动：浅蓝色行是步行、地铁或出租车，后面接到达后的活动。火车、飞机显示具体车次或航班号、出发站或机场、到达站或机场、起止时间与费用；市内接驳显示起终点、方式、时间、沙盒距离和费用。完整 JSON 仍可在每条案例下展开。</p><p class="muted">原始输出中的地铁只有起终站，没有线路号、途经站和换乘细节，页面明确标为未提供。费用为原输出该项合计；每日小计包含活动费用与接驳费用。距离和时刻来自静态沙盒，所有数值保持原样。</p></section>')
    parts.append('<section><h2>14 条总览</h2><div class="scroll"><table><thead><tr><th>案例</th><th>需求</th><th>状态 / 官方评测</th><th>耗时</th><th>每日景点数</th></tr></thead><tbody>')
    for i,c in enumerate(cases,1):
        parts.append(f'<tr><td><a href="#case{i}">{i} · {esc(c["source_split"].replace("_local", ""))}</a></td><td>{esc(c["query"])}</td><td>{esc(c["status"])} / {badge(c["official_scores"]["all_pass"])}</td><td>{esc(c.get("wall_seconds", "运行中"))} 秒</td><td>{esc(c["attractions_per_day"])}</td></tr>')
    parts.append('</tbody></table></div></section>')
    text = ['ChinaTravel / DeepSeek-V4.1-Flash / 普通 NeSy / 14条探索',json.dumps(stats,ensure_ascii=False),'']
    for i,c in enumerate(cases,1):
        text += [f'{i}. {c["uid"]}',c['query'],f'状态 {c["status"]}；官方评测 {badge(c["official_scores"]["all_pass"])}；沙盒费用 {c["sandbox_cost_cny"]} 元；每日景点 {c["attractions_per_day"]}。']
        parts += [f'<article id="case{i}"><h2>案例 {i} · {esc(c["uid"])}</h2><p class="query">{esc(c["query"])}</p>',f'<p>状态：{esc(c["status"])}；格式 {badge(c["official_scores"]["schema"])}，常识 {badge(c["official_scores"]["commonsense"])}，原始逻辑 {badge(c["official_scores"]["logical_py"])}。沙盒费用：{esc(c["sandbox_cost_cny"])} 元。</p>']
        if c['search_timeout']:
            parts.append('<p class="note">上游搜索预算耗尽后返回候选；该状态与原始约束的官方评分分别记录。</p>')
        if not c['has_plan'] and c['status'] not in ('queued','running'):
            parts.append('<p class="note">没有保存完成的行程。官方评测对缺失输出记为不通过；不能把这些缺失输出的逐项失败解释为模型真的生成了对应的错误活动。</p>')
        if c['debug_candidate']:
            parts.append('<details><summary>中断前候选与约束诊断（不计入正式结果）</summary><pre>'+esc(json.dumps(c['debug_candidate'],ensure_ascii=False,indent=2))+'</pre></details>')
        for finding in c['audits']:
            parts.append('<p class="note">观察：'+esc(finding['title'])+'。'+esc(finding.get('case_details',{}).get(c['uid'],''))+'</p>')
        for day in c['plan'].get('itinerary',[]):
            day_activities = day.get('activities', [])
            day_transport_cost = sum(float(t.get('cost', 0)) for a in day_activities for t in a.get('transports', []))
            day_activity_cost = sum(float(a.get('cost', 0)) for a in day_activities)
            parts.append(f'<h3>第 {day["day"]} 天</h3><p class="day-cost">活动与城际交通 {day_activity_cost:.2f} 元 + 市内接驳 {day_transport_cost:.2f} 元 = 当日合计 {day_activity_cost + day_transport_cost:.2f} 元</p><div class="scroll"><table><thead><tr><th>时间 / 时长</th><th>类型</th><th>地点 / 起点 → 终点</th><th>具体信息</th><th>该项费用</th></tr></thead><tbody>')
            text.append(f'  第 {day["day"]} 天')
            for activity in day_activities:
                rows, lines = itinerary_rows(activity)
                parts.append(rows)
                text.extend(lines)
            parts.append('</tbody></table></div>')
        for label,value in [('完整行程 JSON',c['plan']),('模型生成约束',c['generated_constraints']),('原始评测约束（没有提供给模型）',c['reference_constraints']),('官方未通过项',c['official_failures']),('推荐时长差异',c['below_recommended_stay']),('酒店与餐厅数据库信息',{'hotels':c['hotels'],'meals':c['meals']})]:
            parts.append('<details><summary>'+esc(label)+'</summary><pre>'+esc(json.dumps(value,ensure_ascii=False,indent=2))+'</pre></details>')
        parts.append('</article>'); text.append('')
    parts.append('<p class="muted">所有输出保留原样；观察和诊断指标未修改模型生成的 DSL、行程或官方评分。使用本地固定的2026维护版源码和沙盒，不能反推出原论文历史版本也有完全相同的实现表现。沙盒为静态研究数据，未查询实时营业、客流或票务。</p></main></html>')
    publish_report(''.join(parts))
    (OUT/'outputs.txt').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(json.dumps(stats,ensure_ascii=False))


if __name__ == '__main__':
    main()
