"""Validate timing evidence and write the EXP002 report without running a model."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import time


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="EXP002 report directory")
    args = parser.parse_args()
    output = args.out.resolve()
    measured = output / "measurements"
    comparison = read(measured / "COMPARISON.json")
    historical = read(output / "HISTORICAL_TIMING.json")
    manifest = read(measured / "manifest.json")
    source = Path(manifest["source"])
    rows = [read(path) for path in measured.glob("*/*/timing.json")]
    reference = {}
    trace_checks = []
    for row in rows:
        key = (row["variant"], row["candidate_limit"])
        prior = reference.setdefault(key, row)
        for field in ("candidate_sequence_sha256", "dfs_entries", "stop_reason", "counters", "prune_counts"):
            if row[field] != prior[field]:
                raise ValueError(f"Trace mismatch: {key}, {field}")
        if row["llm_api_calls"] or row["llm_cache_hits"] or row["error"]:
            raise ValueError("Unexpected model use or error")
    for (variant, cap), row in reference.items():
        if cap == 1:
            continue
        old = read(source / f"rules_n200_c{cap or 0}" / variant / "summary.json")
        for field in ("dfs_entries", "stop_reason", "counters", "prune_counts", "candidate_count", "best_penalty"):
            if row[field] != old[field]:
                raise ValueError(f"Original trace mismatch: {variant}, {cap}, {field}")
        trace_checks.append({"variant": variant, "candidate_limit": cap, "matches_original_counters": True})
    # Check all outputs, including c1 against the first item of the old c20 sequence.
    for path in measured.glob("*/*/candidates.json"):
        timing = read(path.parent / "timing.json")
        cap = timing["candidate_limit"]
        old_cap = 20 if cap == 1 else cap or 0
        old = read(source / f"rules_n200_c{old_cap}" / timing["variant"] / "candidates.json")
        current = read(path)
        old = old[:1] if cap == 1 else old
        signature = lambda candidates: [(c["fingerprint"], c["score"]["total_penalty"]) for c in candidates]
        if signature(current) != signature(old):
            raise ValueError(f"Candidate sequence differs from EXP001: {path}")
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["variant"], row["candidate_limit"])].append(row)
    timing_rows = []
    for (variant, cap), sample in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or 200)):
        baseline = [r for r in sample if not r["instrumented"]]
        times = [r["elapsed_seconds"] for r in baseline]
        fractions = [(r["elapsed_seconds"] - r["first_candidate"]["elapsed_seconds"]) / r["elapsed_seconds"] for r in baseline]
        timing_rows.append({"variant": variant, "candidate_limit": cap, "baseline_runs": len(times),
            "median_seconds": statistics.median(times), "min_seconds": min(times), "max_seconds": max(times),
            "median_after_first_share": statistics.median(fractions),
            "median_after_first_seconds": statistics.median(r["elapsed_seconds"] - r["first_candidate"]["elapsed_seconds"] for r in baseline),
            "candidate_count": sample[0]["candidate_count"], "dfs_entries": sample[0]["dfs_entries"],
            "search_nodes": sample[0]["counters"]["search_nodes"], "best_penalty": sample[0]["best_penalty"]})
    category_names = {"dsl_constraint_evaluation": "硬约束表达式执行", "route_computation": "本地路线goto计算",
        "ranking": "排序及重排其余工作", "complete_validation": "完整行程校验其余工作",
        "probe_events": "实验事件与回调", "search_remainder": "其他未单列搜索工作",
        "soft_score": "软评分其余工作", "candidate_storage": "候选存储/求最优其余工作",
        "partial_checks": "部分前缀/用餐检查其余工作", "activity_policy": "规则活动决策其余工作"}
    profile = {}
    for variant in ("original", "rank_fixed"):
        sample = [r for r in grouped[(variant, 20)] if r["instrumented"]]
        profile[variant] = {}
        for category, title in category_names.items():
            shares = [r["exclusive_timings"][category]["exclusive_seconds"] / r["attribution_sum_seconds"] for r in sample]
            profile[variant][category] = {"name": title, "median_share": statistics.median(shares),
                "min_share": min(shares), "max_share": max(shares),
                "calls": [r["exclusive_timings"][category]["calls"] for r in sample]}
        profile[variant]["ranking_inclusive_share"] = [r["exclusive_timings"]["ranking"]["inclusive_seconds"] / r["attribution_sum_seconds"] for r in sample]
    offsets = [r["unattributed_search_seconds"] for r in rows if r["instrumented"]]
    analysis = {"run_count": len(rows), "trace_checks": trace_checks, "all_outputs_match_EXP001": True,
        "all_live_model_calls": 0, "timing_rows_all_baselines": timing_rows,
        "default_cap_profiles": profile, "boundary_offset_min_seconds": min(offsets),
        "boundary_offset_max_seconds": max(offsets),
        "clocks": {name: vars(time.get_clock_info(name)) for name in ("monotonic", "perf_counter", "process_time")}}
    destination = output / "ANALYSIS.json"
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    prep = comparison["preparation"]
    lines = ["# EXP002：早期分支集中是不是端到端耗时主因？", "", "日期：2026-11-11。案例：easy_1；冻结状态与EXP001一致。", "",
        "现有证据不支持把早期分支集中判为端到端耗时的主要原因。历史模型准备存在显著开销；冻结后的CPU搜索热点集中在约束执行、路线计算和排序。集中搜索会增加后续计算，却不能仅凭分支占比推断其因果贡献。", "",
        "## 范围与验证", "",
        f"本次完成{len(rows)}次CPU搜索：12种配置各3次常规重复、1次分解（48次），随后因墙钟漂移对original/rank_fixed的20候选配置交错补做8次。另保留一次初步cProfile试跑，不混入正式均值。没有启动GPU或实时请求模型。",
        "全部候选指纹、顺序及总评分与EXP001对应结果一致；c1与原c20第一条一致。相同配置停止原因、DFS次数、原计数器及剪枝计数也一致。",
        f"准备流程精确缓存重放为{prep['wall_seconds']:.3f}秒；11次请求全部命中历史答案，完整冻结字段一致，保留原3次格式回退。导入在本进程另耗时{manifest['import_seconds_single_process']:.3f}秒，只测一次，不能视为热服务每次请求开销。", "",
        "## 历史模型准备开销", "",
        "下表是历史成功请求记录，来自多次准备尝试；不是本次重放耗时，也不是单次端到端总耗时。客户端请求计时包含服务等待等，不等于纯GPU计算。", "",
        "| 请求 | 历史成功请求秒数 |", "| --- | ---: |"]
    for record in sorted(historical["request_records"], key=lambda r: -r["elapsed_seconds"]):
        lines.append(f"| {record['category_zh']} | {record['elapsed_seconds']:.3f} |")
    lines += [f"| 合计（11条历史成功记录） | {prep['historical_successful_request_seconds_sum']:.3f} |", "",
        "最终成功准备本轮仅新增1次请求30.469秒，另10次命中缓存；这30.469秒已经包含在175.611秒中。失败请求未完整计时，酒店约90秒超时仅有当时实验记录。3次格式回退不等于3次网络重试。",
        "这些量级说明模型准备是明确开销，但缺少一次连续、无缓存、保留Qwen活动决策的完整运行，不能给出实时端到端占比。", "",
        "## CPU搜索：常规重复的中位数与范围", "",
        "主矩阵各组3次；original/rank_fixed的c20纳入补测后各5次。分解运行不纳入下面墙钟统计。运行条件存在明显时间漂移，不能仅据中位数认定排序具有显著加速。", "",
        "| 排序 | 候选上限 | 重复 | 搜索中位秒 | 范围秒 | 候选数 | DFS入口 | 首条之后占比中位 | 最佳惩罚 |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |"]
    for r in timing_rows:
        lines.append(f"| {r['variant']} | {r['candidate_limit'] or '无限，200入口停止'} | {r['baseline_runs']} | {r['median_seconds']:.3f} | {r['min_seconds']:.3f}–{r['max_seconds']:.3f} | {r['candidate_count']} | {r['dfs_entries']} | {r['median_after_first_share']:.1%} | {r['best_penalty']:.6f} |")
    lines += ["", "所有组第一条候选已经达到该组最终最佳评分；继续搜索未改善这一评分。前三种排序为0分；third_first一直约20.43分。更多候选仍可能增加多样性，因此首条后的时间不是已验证可无损删除的时间。",
        "原始EXP001中，20候选时首条之后占21.42%–29.89%；取消上限并跑满200入口时占69.30%–76.39%。后者不能代表默认20候选流程。",
        "third_first在20候选下只需29次DFS/831个原search_nodes，rank_fixed为33/1197；但前者质量更差。差开头并不必然让计算更慢，可能更快地产出较差方案。", "",
        "## 默认20候选：搜索内部时间花在哪里", "",
        "下表为每种排序3次分解的exclusive占比中位数。exclusive扣除了被计时的子调用，避免嵌套重复归因；各类别中位数的和可能因取中位数略偏离100%。", "",
        "| 工作 | original | rank_fixed | original调用次数 | rank_fixed调用次数 |",
        "| --- | ---: | ---: | ---: | ---: |"]
    for category, title in category_names.items():
        a, b = profile["original"][category], profile["rank_fixed"][category]
        lines.append(f"| {title} | {a['median_share']:.2%} | {b['median_share']:.2%} | {a['calls'][0]} | {b['calls'][0]} |")
    lines += ["", "地点基础排序与约束重排合计6次；每次扫描全表，会计算路线、构造临时行程并执行约束。其inclusive占比original约67%，rank_fixed约69%——已经包含表中的部分路线和约束时间，不能再次相加。",
        "676次evaluate_constraints_py并不等于676个DFS节点：相当一部分发生在候选重排。原cProfile也确认execute_dsl_code反复验证AST、编译并执行，且存在函数字典/行程复制。",
        "goto是本地坐标、测地距离及地铁图计算，不是实时地图网络接口。该项没有包含所有WorldEnv字符串解析成本。",
        "search_remainder包含其他循环、复制、部分前缀检查和日志处理；不能将其叫作纯递归耗时。probe_events也不涵盖所有程序日志。软评分很便宜，并非本例主要CPU热点。", "",
        "## 测量误差与范围", "",
        f"分项perf_counter总和与原summary的monotonic区间差值为{min(offsets):.6f}至{max(offsets):.6f}秒。当前Python的monotonic使用GetTickCount64，分辨率15.625毫秒；perf_counter使用高分辨率QPC。因此毫秒级负差值不能解释成重复计时，未做截零隐藏。",
        "主矩阵后半程明显变慢，不能将分解/基线的约2倍差异全部归因于插桩。后续交错基线与分解耗时接近；SUPPLEMENTAL.json保留各次进程CPU时间、墙钟和完整结果。原因未隔离，不宣称为某一系统因素所致。",
        "准备捕获前已做首次景点基础排序；冻结后的DFS会再做一次。准备还额外做餐厅推荐冻结。因此不能将准备重放、冻结搜索和历史模型记录相加并称为实测全程。", "",
        "## 对原假设的判断", "",
        "1. 支持：同一早期分支可以占用几乎全部入口预算；搜索覆盖与行程质量对首地点敏感。",
        "2. 支持：首个候选后的继续搜索增加了CPU计算，本例最优评分没有改善；增加幅度强烈依赖候选停止条件。",
        "3. 支持：当前规则搜索主要计算热点是反复排序、约束执行和路线计算；历史模型准备请求具有明显延迟。",
        "4. 不支持：把199/200入口位于某分支等同于99.5%的端到端时间都被坏开头浪费。入口次数不是时间；分支耗时包含各种工作；早期分支也可能已经达到当前评分下界。",
        "5. 尚未测量：真实Qwen搜索里，分支选择会怎样改变模型请求数、提示词长度与总延迟；调整分支间预算能否在相同质量和输出要求下更快达到目标。", "",
        "## 后续应分开的两个问题", "",
        "质量问题：是否需要更早比较不同区域/开头，避免在不合适的框架里细化。",
        "延迟问题：模型准备、搜索中模型决策、每个节点的评估成本、找到可用结果后的继续搜索分别占多少。不能因为质量问题成立，就认为换掉DFS会解决主要延迟。",
        "真实运行应从请求入口统一计时，记录准备CPU/模型、搜索CPU/模型、输出及失败请求；冷启动单列。之后在相同质量目标、输出数量和独立多案例上做预算分配对照，才能给因果结论。本轮保持GPU释放，没有补跑该实时模型实验。", "",
        "## 复现与证据", "", "```powershell",
        ".\\.venv\\Scripts\\python.exe -X utf8 experiments/timing_probe/run.py --out artifacts/timing_probe/EXP002_new/measurements --repeats 3",
        ".\\.venv\\Scripts\\python.exe -X utf8 experiments/timing_probe/repeat_default.py --out artifacts/timing_probe/EXP002_new/measurements",
        "```", "",
        "历史账本见HISTORICAL_TIMING.json/.md；本次原始结果见measurements/，分解汇总见ANALYSIS.json；主记录见EXPERIMENT_LOG.md。analyze.py读取账本及测量目录生成此报告；不会调用模型。", ""]
    report = output / "REPORT.md"
    if report.exists():
        raise FileExistsError(report)
    report.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"runs": len(rows), "all_outputs_and_counters_match": True, "report": str(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
