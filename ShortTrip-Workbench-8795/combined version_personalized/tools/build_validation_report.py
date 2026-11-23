"""Summarize observed outputs and independently recheck the displayed plans."""
from pathlib import Path
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chinatravel.personalization.cli import environment,read,save


def main():
    env=environment(ROOT.parent/'ShortTrip/artifacts/data/5e0ee8a')
    from chinatravel.personalization.catalog import PreferenceCatalog
    from chinatravel.personalization.evaluation import evaluate_preferences
    from chinatravel.personalization.compiler import compile_request
    from experiments.evaluation.validation import validate_plan
    from chinatravel.soft_constraints.pool import fingerprint
    output=ROOT/'result/experiments/P001_report';output.mkdir(exist_ok=True)
    cases=[]
    for case in ['ordinary','museum','four_day_appointment']:
        folder=ROOT/'result/experiments/P001_live_v4'/case
        request=read(folder/'request.json');plan=read(folder/'plan.json')
        status=read(folder/'STATUS.json');run=read(folder/'planning/run.json')
        p=evaluate_preferences(plan,request,PreferenceCatalog(env,request['trip']['destination_city']))
        audit=validate_plan(plan,compile_request(request)['legacy_query'],config={'time_policy':'flexible'})
        legal=all(c['status'] in ('pass','not_applicable') for c in audit['checks']) and p['eligible']
        assert fingerprint(plan)==status['selected_fingerprint']
        cases.append({'case':case,'posthoc_legal':legal,'first_legal_seconds':status['first_independent_legal_seconds'],
            'delivery_seconds':status['end_to_end_seconds'],'planner_wall_seconds':run['wall_seconds'],
            'model_calls':run['request_count'],'internal_candidates':status['internal_candidates'],
            'independent_legal_candidates':status['independent_legal_candidates'],
            'selected_fingerprint':status['selected_fingerprint'],'preferences':p,'hard_audit':audit})
    conversion=read(ROOT/'result/experiments/P001_conversion_v4/SUMMARY.json')
    rows=[json.loads(line) for line in (ROOT/'result/experiments/P001_conversion_v4/requests.jsonl').read_text(encoding='utf-8').splitlines()]
    errors=[]
    for r in rows:
        if r['ready']:
            try:compile_request(r)
            except (ValueError,TypeError,KeyError) as error:errors.append({'source':r['source'],'type':type(error).__name__})
    record={'cases':cases,'conversion':conversion,'compiled_ready_requests':sum(r['ready'] for r in rows),'compile_errors':errors,
            'base_rows':604,'base_rows_longer_than_three_days':72,'total_source_records_including_duplicates':len(rows),
            'live_model':'deepseek-flash','case_seconds':120,'case_candidate_target':3,
            'live_batch':'P001_live_v4','scope':'pilot_not_population_quality_or_speed_conclusion'}
    save(output/'RESULTS.json',record)
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for directory in ['chinatravel/personalization','experiments/timing_probe']
            for p in (ROOT/directory).glob('*.py')}
    save(output/'DELIVERY_SOURCE_HASHES.json',{'meaning':'delivery version, not a retrospective pre-run snapshot','files':hashes})
    lines=['# P001 个性化版本验证','',
        '2026-11-19。旅行天数取消1–3天限制。真实模型为官方deepseek-flash，固定十一城快照、flexible时间政策，'
        '每例120秒总搜索预算、目标3候选；与默认1800秒/10候选区分。无偏好与博物馆两例只有profile不同，API无seed，单次配对不证明总体增益。','',
        '| 真实请求 | 首份独立合法（测量入口起）/秒 | 核验前处理/秒 | 模型请求 | 独立合法候选 | 最终核验 |',
        '|---|---:|---:|---:|---:|---|']
    for c in cases:
        lines.append(f"| {c['case']} | {c['first_legal_seconds']:.2f} | {c['delivery_seconds']:.2f} | {c['model_calls']} | {c['independent_legal_candidates']}/{c['internal_candidates']} | {'通过' if c['posthoc_legal'] else '失败'} |")
    lines+=['','首份独立合法时间来自测量入口到候选生成的实际时刻，独立审核在生成后执行，未把参考答案提供给搜索。核验前处理含父进程环境设置、子进程搜索和原输出工作；本轮旧字段end_to_end_seconds在最终审核前取值，因此表中不称其为完整交付耗时。当前代码已修正为审核完成后计时，未反推或覆盖历史时间。','',
        '| 实际选中方案 | 总费/元 | 接驳步行/分 | 市内通行/分 | 游览/分 | 景点数 |', '|---|---:|---:|---:|---:|---:|']
    for c in cases:
        m=c['preferences']['metrics']
        lines.append(f"| {c['case']} | {m['total_cost']:.2f} | {m['access_walk_minutes']} | {m['city_transit_minutes']} | {m['sightseeing_minutes']} | {m['unique_attractions']} |")
    lines+=['',
        '博物馆偏好组实际安排闵行博物馆和上海地铁博物馆，属性证据命中；普通组也有上海纺织博物馆。'
        '偏好组更便宜、接驳更少，但总游览从270分钟降至156分钟，不能解释为各方面都更好。两个两天方案第二天均无景点，属仍需改善的行程体验。','',
        '四天案例第三天上海博物馆15:00–16:00预约实际保留。交付时重新执行独立硬审核和新增预约检查，实际选案指纹一致。'
        '舒缓评分目前使用平均占用的代理指标，不能证明每天都舒适。景区内部步行仍未知。','',
        '## 转换与离线验证','',
        f"1044条来源记录全部转换，包含604条基础题、300条偏好变体、50条基础副本、30条回归、60条西安题。"
        f"72条4–6天基础题保留。{sum(r['ready'] for r in rows)}条当前可执行字段ready，其模板编译错误{len(errors)}；ready不是完整自然语言理解准确率。",
        '', '转换产物在 ../P001_conversion_v4/，原文、未映射要求与参考代码分开。每个分层的待确认数及未知项见SUMMARY和conversion_audit；未跑1044条完整搜索。', '',
        '历史20份真实候选重排在 ../P001_candidate_replay_final/。博物馆和少走路会改变选择，少走路只少1分钟；自然风光无匹配，住宿费用不变。'
        '这验证选案逻辑，也说明既有候选池不足以提供所有偏好选择。', '',
        '固定历史推荐ID的真实数据搜索在 ../P001_fixture_smoke_v2/，生成2份候选且选案合法，API调用0。它只验证调用链，不计作真实模型性能证据。', '',
        '桌面1366和手机390宽度的真实浏览器操作在 ../P001_browser/output/playwright/。核对6天和博物馆偏好、文本与表单冲突、缺少模型服务提示，无横向溢出。'
        '第一轮用无密钥8782服务检验缺服务提示；后续8783有密钥服务在真实浏览器完成从偏好表单到已核验行程的完整过程，记录与截图在../P001_browser_live/output/playwright。两轮均检查桌面和手机。', '',
        '最终相关个性化、旧评分、日集合、组合调度与候选数检查42项通过，日志为../P001_regression_final.log。', '',
        '## 失败、修正和局限','',
        '- live_v1/v2：本机响应压缩解码TypeError，禁用响应压缩后预检通过，未改全局依赖。',
        '- live_v3：三例在数据初始化前失败。将数据配置放在偏好/DSL模块导入之前，clean_process_prepare与live_v4复测通过。',
        '- conversion_v1/v2/v3保留。修正“不要特种兵”否定、仅去程火车作用域、若干已有偏好同义表达与接驳/全日步行区别。',
        '- 原题标签与原文有差异。例如部分p1原文只有多去景点，未提少交通；不从标签或preference_py补入隐藏需求。',
        '- 245个基线文件已复制并记录初始哈希；原目录另有并行任务修改sequence_flex的4个文件，此版本使用复制时内容，不追随外部工作树变化。',
        '- 未对更多真实用户做满意度校准，也未证明长天数的普遍合法率、速度或无质量退化。需要按base_uid分组扩大配对与盲评。',
        '', '当前代码和结果位置见 ../../../README.md 与 ../../../EXPERIMENT_LOG.md。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(output/'REPORT.md'),'all_live_selected_legal':all(c['posthoc_legal'] for c in cases),'compile_errors':len(errors)},ensure_ascii=False))


if __name__=='__main__':main()
