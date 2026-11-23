"""Readable reports; distinguish observed legacy outputs from strict timed results."""
from .common import number


def fmt(value, digits=2):
    return f"{value:.{digits}f}" if number(value) else "—"


def report_text(manifest, summaries, pairs, rows):
    lines = ["# ShortTrip 独立评分报告", "", f"评分配置：`{manifest['profile']}`；版本：`{manifest['evaluation_version']}`。",
             "状态：工程代理评分，尚未完成人工偏好校准。分数改善不等于已证明用户满意度改善。", "",
             f"运行数：{len(rows)}；独立需求数：{len({r.get('query_group', r['query_id']) for r in rows})}。",
             "合法性为独立规则复核；未知数据不补为理想值。无偏好显示N/A；语义评分未完成时完整分留空。", ""]
    if any(r["protocol"] == "legacy_posthoc" for r in rows):
        lines += ["本报告包含历史回放：旧产物没有方案发布时间，因此不进入严格预算内合法率或严格预算胜率的分母。旧耗时仅是冻结后的搜索区间，不是端到端时延。", ""]
    lines += ["| 方法 | 范围 / 协议 | 已运行 / 计划 | 独立需求 | 合法 / 非法 / 无结果 / 未知 | 预算内合法率 | Q | P | Q已知 / P已知 | 搜索或规划秒中位数 |",
              "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- | ---: |"]
    for s in summaries:
        counts = s["validity_counts"]
        count_text = "/".join(str(counts.get(k, 0)) for k in ("valid", "invalid", "no_result", "unknown"))
        rate = f"{s['feasible_by_budget']:.1%}" if number(s["feasible_by_budget"]) else "不适用"
        lines.append(f"| {s['method']} | {s['scope']} / {s['protocol']} | {s['executed_runs']}/{s['planned_runs']} | {s['query_count']} | {count_text} | {rate} | {fmt(s['quality_mean'])} | {fmt(s['preference_mean'])} | {s['quality_known_runs']}/{s['preference_known_runs']} | {fmt(s['planning_seconds_median'],3)} |")
    lines += ["", "## 相同需求的配对比较", "",
              "历史回放只比较原方法实际选中的最终方案；不表示预算内交付表现。候选池事后最佳若计算，只作诊断。",
              "主确认获胜占比W/N不将平局折半；双方失败和未决单列。统计先在需求内平均，再对需求等权。", "",
              "| 新方法 | 基线 | 胜 / 平 / 负 / 双失败 / 未决 | 确认获胜占比 | 共同合法配对数 | 配对Q差 | 配对P差 | 未匹配 |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    baseline = manifest.get("baseline", "—")
    for s in summaries:
        if s["method"] == baseline:
            continue
        counts = s["comparison_counts"]
        count_text = "/".join(str(counts.get(k,0)) for k in ("win","tie","loss","both_failed","unresolved"))
        lines.append(f"| {s['method']} | {baseline} | {count_text} | {fmt(s['confirmed_win_rate'])} | {s['common_valid_count']} | {fmt(s['paired_quality_difference'])} | {fmt(s['paired_preference_difference'])} | {s['unmatched_count']} |")
    lines += ["", "## 逐运行诊断", "",
              "| 运行 | 合法性 | Q | P状态 | S | 原罚分↓ | 停止原因 | 首次合法时间 | 测量问题 |",
              "| --- | --- | ---: | --- | ---: | ---: | --- | ---: | --- |"]
    for r in rows:
        lines.append(f"| {r['run_id']} | {r['validity']} | {fmt(r.get('quality_score'))} | {r.get('preference_status','—')} | {fmt(r.get('composite_score'))} | {fmt(r.get('legacy_penalty'))} | {r.get('stop_reason','—')} | {fmt(r.get('first_valid_seconds'),3)} | {', '.join(r.get('measurement_issues',[])) or '无'} |")
    lines += ["", "## 成本与证据", "",
              f"本次离线评测墙钟：{manifest['evaluation_seconds']:.3f} 秒；真实语义请求数：{manifest['judge_request_count']}；复用计划评分缓存：{manifest['plan_cache_hits']} 次。",
              "完整输入文件哈希、评分配置与实现哈希见manifest.json；独立校验、分项和覆盖率见plan_scores.jsonl；停止原因见run_metrics.jsonl；匹配证据见pairs.jsonl。",
              "预算曲线数据见budget_curve.jsonl：只取时点前已发布的方案，分数允许下降，不用事后最佳包络替代。",
              "同一需求的多个排序或重复不能视为多个独立需求。单需求报告不提供泛化置信区间。",
              "人工验证接口可导出盲选材料；未获得人工标签前，一致率保持空值。", ""]
    return "\n".join(lines)
