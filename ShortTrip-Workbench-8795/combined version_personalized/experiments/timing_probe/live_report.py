"""Summarize live paired profiles, independent validity, and EXP006 request evidence."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from statistics import median
import sys
import time


PROJECT = Path(__file__).resolve().parents[2]
HISTORY = PROJECT / "artifacts/live_c/EXP006_five_1794410761016/ShortTrip/experiments/c_only"
sys.path.insert(0, str(PROJECT))

MODULES = {
    "进程导入与环境初始化": ("imports", "environment_and_model_setup"),
    "需求翻译本地处理": ("translation",),
    "城际与地点数据读取": ("intercity_db", "poi_db"),
    "交通路线查询与计算": ("route_lookup", "route_compute"),
    "推荐、重排与活动选择的本地工作": ("outbound_ranking", "return_ranking", "hotel_ranking",
                                "attraction_ranking", "restaurant_ranking", "local_mode_ranking",
                                "room_inference", "budget_inference", "activity_type",
                                "visit_duration", "constraint_reranking"),
    "剪枝和用餐窗口": ("partial_pruning", "meal_windows", "meal_lookahead"),
    "整程校验": ("full_validation", "commonsense_validation", "dsl_validation",
             "symbolic_concepts", "c_rule_audit"),
    "软评分与候选池": ("soft_scoring", "candidate_pool"),
    "DFS 其余工作": ("dfs",),
    "城际/住宿外层搜索及其余规划": ("symbolic_search", "outer_search", "planning"),
    "日志和结果写出": ("planner_log_write", "result_io", "output"),
}
PHASE_NAMES = {
    "translation": "需求翻译", "outbound_ranking": "去程推荐", "return_ranking": "返程推荐",
    "hotel_ranking": "酒店推荐", "local_mode_ranking": "市内交通方式推荐",
    "room_inference": "房间数与房型", "budget_inference": "预算识别",
    "attraction_ranking": "景点推荐", "restaurant_ranking": "餐厅推荐",
    "activity_type": "下一活动类型", "visit_duration": "游览时长", "other": "其他请求",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify(path, query, internal):
    if not path.is_file() or not (plan := read(path)).get("itinerary"):
        return {"common": "none", "C_audit": None, "internal": internal}
    from experiments.evaluation.validation import validate_plan

    evidence = validate_plan(plan, query)
    statuses = {row["name"]: row["status"] for row in evidence["checks"]}
    c_rules = statuses.pop("C_audit", None)
    common = "fail" if "fail" in statuses.values() else "unknown" if "unknown" in statuses.values() else "pass"
    return {"common": common, "C_audit": c_rules, "internal": internal,
            "issues": {name: status for name, status in statuses.items() if status != "pass"},
            "evidence": evidence}


def historical(root):
    if not (root / "process_exits.json").is_file():
        return []
    exits = read(root / "process_exits.json")
    rows = []
    for trace in sorted((root / "traces").glob("*.jsonl")):
        requests = jsonl(trace)
        activity = [r for r in requests if r["stage"] == "activity_type"]
        seen, repeats, repeat_seconds = set(), 0, 0.
        for request in activity:
            key = json.dumps(request["messages"], sort_keys=True, ensure_ascii=False)
            if key in seen:
                repeats += 1
                repeat_seconds += request["elapsed_seconds"]
            seen.add(key)
        total = sum(r["elapsed_seconds"] for r in requests)
        status = read(root / "status" / f"{trace.stem}.json")
        rows.append({"case": trace.stem, "state": status["state"] if trace.stem in exits else "interrupted",
                     "wall_seconds": exits.get(trace.stem, {}).get("elapsed_seconds"),
                     "request_count": len(requests), "llm_seconds": total,
                     "activity_llm_seconds": sum(r["elapsed_seconds"] for r in activity),
                     "activity_requests": len(activity), "repeated_activity_requests": repeats,
                     "repeated_activity_seconds": repeat_seconds})
    return rows


def profile(case_dir, query):
    base = case_dir / "profile"
    row = read(base / "run.json")
    stats = row["stats"]
    requests = jsonl(base / "requests.jsonl")
    by_phase = defaultdict(lambda: {"calls": 0, "seconds": 0.0, "output_tokens": 0})
    for request in requests:
        group = by_phase[request["phase"]]
        group["calls"] += 1
        group["seconds"] += request["seconds"]
        group["output_tokens"] += request["output_tokens"]
    assert len(requests) == row["request_count"]
    row["llm_seconds"] = sum(values["exclusive_seconds"] for name, values in stats.items() if name.startswith("llm/"))
    row["phase_requests"] = dict(by_phase)
    row["module_seconds"] = {label: sum(stats.get(name, {}).get("exclusive_seconds", 0) for name in names)
                             for label, names in MODULES.items()}
    leftover = row["wall_seconds"] - row["llm_seconds"] - sum(row["module_seconds"].values())
    row["module_seconds"]["未单列"] = leftover
    if abs(leftover - row["unattributed_seconds"]) > .05:
        raise ValueError(f"Missing exclusive stage in {case_dir}")
    row["dfs_inclusive_seconds"] = stats.get("dfs", {}).get("inclusive_seconds", 0)
    first = row["first_published_seconds"]
    row["first_llm_seconds"] = sum(r["seconds"] for r in requests if r["started_seconds"] + r["seconds"] <= first) if first else None
    audit_started = time.perf_counter()
    row["first_audit"] = verify(base / "first_plan.json", query, first is not None)
    row["selected_audit"] = verify(base / "plan.json", query, row["internal_success"])
    row["posthoc_audit_seconds"] = time.perf_counter() - audit_started
    server = (case_dir / "server.log").read_text(encoding="utf-8", errors="replace")
    ready = re.search(r"^(\d+)\.(\d+)\.(\d+)\.(\d+) .*server_on_li: listening", server, re.M)
    row["server_startup_seconds"] = None
    if ready:
        minutes, seconds, milliseconds, microseconds = map(int, ready.groups())
        row["server_startup_seconds"] = 60 * minutes + seconds + milliseconds / 1000 + microseconds / 1e6
    row["server_seconds"] = {phase: sum(map(float, re.findall(pattern, server, re.M))) / 1000
                             for phase, pattern in {
                                 "prompt_evaluation": r"prompt eval time\s*=\s*([\d.]+) ms",
                                 "generation": r"\|\s+eval time\s*=\s*([\d.]+) ms",
                             }.items()}
    return row


def fmt(value):
    return f"{value:.2f}" if value is not None else "—"


def report(manifest, rows, history, missing):
    pairs = [(case, arm, row) for case, methods in rows.items() for arm, row in methods.items()]
    names = {"native": "ChinaTravel 原版", "shorttrip": "ShortTrip C 组"}
    titles = [f"{case} {names[arm]}" for case, arm, _ in pairs]
    title = "EXP010 原有 DFS 实时耗时归因" if manifest.get("stop_mode") == "original" else "EXP008 实时耗时归因"
    lines = [f"# {title}", "",
             "每个方案使用同一份需求、本地 Qwen 权重和沙盒。"
             f"统一端到端截止 {manifest['seconds']:g} 秒，模型输出上限 {manifest['max_tokens']}。"
             "每次搜索前重启同配置模型服务，服务启动和事后独立审核单列。", "",
             "## 配对结果", "",
             "| 案例 | 版本 | 总墙钟秒 | 进程 CPU 秒 | 首次内部候选秒 | 首份共同审核 | 选中共同审核 | C 组规则审核 | 候选 | 模型秒 / 调用数 | 停止原因 |",
             "| --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | --- | --- |"]
    for case, arm, row in pairs:
        selected = row["selected_audit"]
        lines.append(f"| {case} | {names[arm]} | {fmt(row['wall_seconds'])} | {fmt(row['cpu_seconds'])} | "
                     f"{fmt(row['first_published_seconds'])} | {row['first_audit']['common']} | "
                     f"{selected['common']} | {selected['C_audit'] or '—'} | {row['candidate_count']} | "
                     f"{fmt(row['llm_seconds'])} / {row['request_count']} | {row['stop_reason']} |")
    if missing:
        lines += ["", "尚无完整计时记录的组为 " + "、".join(missing) + "。"]
    lines += ["", "原版找到首份内部合法方案即停止。C 组继续收集最多 20 条。"
              "首份方案经同一套数据集硬约束、格式与常识独立核查。C 组三餐、日期等额外规则单列。"]
    if manifest.get("stop_mode") == "original":
        lines.append("本轮保留各版本内部停止条件。ShortTrip C 组保留 1750 秒内部限时，"
                     "ChinaTravel 保留原有 TIME_CUT 及其计算方式。1800 秒是外层共同截止。")
    lines += ["", "## 独占耗时（秒）", "",
              "| 模块 | " + " | ".join(titles) + " |",
              "| --- | " + " | ".join("---:" for _ in titles) + " |"]
    for label in ("模型请求", *MODULES, "未单列"):
        cells = [fmt(row["llm_seconds"] if label == "模型请求" else row["module_seconds"][label])
                 for _, _, row in pairs]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "独占耗时扣除已测子调用，可以相加。DFS 包含耗时会覆盖递归中触发的模型、路线和校验。"
              "DFS 其余工作包含回溯、复制和其它未拆分的搜索逻辑。", "",
              "## 模型请求分用途", "",
              "| 用途 | " + " | ".join(titles) + " |",
              "| --- | " + " | ".join("---:" for _ in titles) + " |"]
    for phase, label in PHASE_NAMES.items():
        cells = []
        for _, _, row in pairs:
            item = row["phase_requests"].get(phase, {})
            cells.append(f"{fmt(item['seconds'])} 秒 / {item['calls']} 次" if item else "—")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## 首份候选前与搜索区间", "",
              "| 运行 | 首份前模型秒 | 首份后至结束秒 | DFS 包含秒 | 服务端预填充秒 | 服务端生成秒 | 模型服务启动秒 | 事后独立审核秒 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for title, (_, _, row) in zip(titles, pairs):
        first = row["first_published_seconds"]
        after = row["wall_seconds"] - first if first else None
        lines.append(f"| {title} | {fmt(row['first_llm_seconds'])} | {fmt(after)} | "
                     f"{fmt(row['dfs_inclusive_seconds'])} | {fmt(row['server_seconds']['prompt_evaluation'])} | "
                     f"{fmt(row['server_seconds']['generation'])} | {fmt(row['server_startup_seconds'])} | "
                     f"{fmt(row['posthoc_audit_seconds'])} |")
    lines += ["", "服务端预填充与生成属于模型请求的内部统计。未完成请求的内部统计可能缺失。", "",
              "## EXP006 生产配置的历史账本", "",
              "| 案例 | 状态 | 总墙钟秒 | 模型请求秒 / 次数 | 下一活动类型秒 / 次数 | 重复活动请求秒 / 次数 | 模型墙钟占比 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in history:
        wall = row["wall_seconds"]
        share = f"{row['llm_seconds'] / wall:.1%}" if wall else "—"
        lines.append(f"| {row['case']} | {row['state']} | {fmt(wall)} | "
                     f"{fmt(row['llm_seconds'])} / {row['request_count']} | "
                     f"{fmt(row['activity_llm_seconds'])} / {row['activity_requests']} | "
                     f"{fmt(row['repeated_activity_seconds'])} / {row['repeated_activity_requests']} | {share} |")
    lines += ["", "历史中断行只累计已落盘的请求。重复活动请求按同一案例中完整 messages 相同计算，秒数为实际观察值。"
              "EXP008 每条需求每组一次，同预算超时按截止结果比较。", ""]
    return "\n".join(lines)


def full_report(manifest, rows, missing):
    arms = {"shorttrip": "ShortTrip", "native": "ChinaTravel"}
    samples = {
        (tier, arm): [rows[case][arm] for case in cases if arm in rows[case]]
        for tier, cases in manifest["tiers"].items() for arm in arms
    }
    count = sum(map(len, manifest["tiers"].values()))
    lines = ["# EXP009 三组全量实时计时", "",
             f"共 {count} 条需求，每条使用同一份输入分别运行两个版本。规划截止 {manifest['seconds']:g} 秒。",
             "原版在首份内部合法方案后停止，ShortTrip 最多收集 20 条。"
             "共同独立审核包括数据集硬约束、格式和常识。", "",
             "| 分组 | 版本 | 计时记录 / 计划 | 通过共同审核 | 达到时限 | 运行错误 | 总耗时中位数（秒） | 模型占比中位数 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for (tier, arm), group in samples.items():
        passed = sum(row["selected_audit"]["common"] == "pass" for row in group)
        cutoff = sum(row["stop_reason"] in ("matched_wall_budget", "wall_limit_reached") for row in group)
        errors = sum(row["stop_reason"] == "error" for row in group)
        wall = fmt(median(row["wall_seconds"] for row in group)) if group else "—"
        share = f"{median(row['llm_seconds'] / row['wall_seconds'] for row in group):.1%}" if group else "—"
        lines.append(f"| {tier} | {arms[arm]} | {len(group)}/{len(manifest['tiers'][tier])} | "
                     f"{passed} | {cutoff} | {errors} | {wall} | {share} |")
    headings = [f"{tier} {arms[arm]}" for tier, arm in samples]
    lines += ["", "## 模块独占耗时中位数（秒）", "",
              "| 模块 | " + " | ".join(headings) + " |",
              "| --- | " + " | ".join("---:" for _ in headings) + " |"]
    for label in ("模型请求", *MODULES, "未单列"):
        values = [fmt(median(r["llm_seconds"] if label == "模型请求" else r["module_seconds"][label]
                             for r in group)) if group else "—" for group in samples.values()]
        lines.append(f"| {label} | " + " | ".join(values) + " |")
    lines += ["", "## 模型请求分用途累计耗时（秒）", "",
              "| 用途 | " + " | ".join(headings) + " |",
              "| --- | " + " | ".join("---:" for _ in headings) + " |"]
    for phase, label in PHASE_NAMES.items():
        values = [fmt(sum(r["phase_requests"].get(phase, {}).get("seconds", 0) for r in group))
                  if group else "—" for group in samples.values()]
        lines.append(f"| {label} | " + " | ".join(values) + " |")
    lines += ["", "## 逐条结果", "",
              "逐条模块耗时、模型用途、审核证据和源文件版本见 [ANALYSIS.json](ANALYSIS.json)。"]
    for tier, cases in manifest["tiers"].items():
        lines += ["", f"### {tier}", "",
                  "| 案例 | 版本 | 总秒 | 模型秒 / 调用 | 首次内部候选秒 | 共同审核 | C 规则 | 候选数 | 停止原因 |",
                  "| --- | --- | ---: | ---: | ---: | --- | --- | ---: | --- |"]
        for case in cases:
            for arm in arms:
                if arm not in rows[case]:
                    lines.append(f"| {case} | {arms[arm]} | — | — | — | — | — | — | 无完整记录 |")
                    continue
                row = rows[case][arm]
                audit = row["selected_audit"]
                timing = f"../{case}/{arm}/profile/run.json"
                lines.append(f"| [{case}]({timing}) | {arms[arm]} | {fmt(row['wall_seconds'])} | "
                             f"{fmt(row['llm_seconds'])} / {row['request_count']} | "
                             f"{fmt(row['first_published_seconds'])} | {audit['common']} | {audit['C_audit'] or '—'} | "
                             f"{row['candidate_count']} | {row['stop_reason']} |")
    if missing:
        lines += ["", "无完整计时的运行见 ANALYSIS.json 的 missing 字段。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--also", type=Path, nargs="*", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = read(args.run / "manifest.json")
    rows, missing, read_errors = {}, [], {}
    full = manifest.get("scope") == "full_local_datasets"
    runs = []
    for directory in (args.run, *args.also):
        batch = read(directory / "manifest.json")
        for key in ("sources", "seconds", "max_tokens", "seed", "temperature", "backend", "data_sha256"):
            if batch[key] != manifest[key]:
                raise ValueError(f"Compared batches differ on {key}")
        if batch.get("stop_mode", "matched_cutoff") != manifest.get("stop_mode", "matched_cutoff"):
            raise ValueError("Compared batches use different stopping protocols")
        for script in ("live_profile.py", "spans.py"):
            if batch["harness_sha256"][script] != manifest["harness_sha256"][script]:
                raise ValueError(f"Compared batches differ on {script}")
        runs.append({"directory": str(directory.resolve()), "cases": batch["cases"]})
        for case in batch["cases"]:
            if case in rows:
                raise ValueError(f"Duplicate case {case}")
            query = read(directory / "queries" / f"{case}.json")
            rows[case] = {}
            for arm in ("shorttrip", "native"):
                case_dir = directory / case / arm
                if not (case_dir / "profile/run.json").is_file():
                    missing.append(f"{case} {arm}")
                    continue
                try:
                    rows[case][arm] = profile(case_dir, query)
                except Exception as error:
                    if not full:
                        raise
                    key = f"{case} {arm}"
                    missing.append(key)
                    read_errors[key] = f"{type(error).__name__}: {error}"
    history = historical(HISTORY)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "ANALYSIS.json").write_text(json.dumps(
        {"manifest": manifest, "batches": runs, "runs": rows, "historical": history,
         "missing": missing, "read_errors": read_errors},
        ensure_ascii=False, indent=2), encoding="utf-8")
    content = full_report(manifest, rows, missing) if full else report(manifest, rows, history, missing)
    (args.out / "REPORT.md").write_text(content, encoding="utf-8")
    print(args.out / "REPORT.md")


if __name__ == "__main__":
    main()
