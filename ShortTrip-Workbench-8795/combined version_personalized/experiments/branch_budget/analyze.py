"""Audit EXP003 publications and compare matched search budgets."""

import argparse
from collections import Counter
from pathlib import Path
from statistics import median

from experiments.evaluation.common import PROJECT, read_json, read_jsonl, write_json
from experiments.evaluation.run import evaluate
from experiments.branch_budget.breakdown import write_breakdown


HISTORICAL = PROJECT / "artifacts/prefix_probe/EXP001_easy_1/rules_n200_c0"
HISTORICAL_ORDERS = {"123": "rank_fixed", "213": "second_first", "312": "third_first"}


def check_baseline(rows):
    checks = {}
    for row in rows:
        variant = HISTORICAL_ORDERS.get(row["order"])
        if row["method"] != "dfs" or row["node_budget"] != 200 or not variant:
            continue
        historical = read_json(HISTORICAL / variant / "candidates.json")
        expected = [[c["fingerprint"], c["score"]["total_penalty"]] for c in historical]
        checks[row["order"]] = {
            "historical_variant": variant, "historical_candidates": len(expected),
            "new_candidates": row["candidate_count"],
            "identical_candidate_sequence": row["candidate_signature"] == expected,
        }
    return checks


def typical(rows, field):
    values = [r[field] for r in rows if r.get(field) is not None]
    return median(values) if values else None


def values_text(values):
    return " / ".join("—" if v is None else f"{v:.2f}" for v in values)


def penalty_outcome(base, trial):
    if trial["validity"] == "unknown" or base["validity"] == "unknown":
        return "unknown"
    if trial["validity"] != "valid":
        return "loss" if base["validity"] == "valid" else "both_failed"
    if base["validity"] != "valid":
        return "win"
    a, b = base["best_penalty"], trial["best_penalty"]
    return "win" if b < a else "loss" if b > a else "tie"


def summarize(rows, scored, plans):
    scored_by_id = {r["run_id"]: r for r in scored}
    selected_scores = {r["run_id"]: r["scores"] for r in plans if r["role"] == "selected"}
    pairs = {}
    for row in rows:
        score = scored_by_id[row["run_id"]]
        if sum(row["branch_entries"].values()) != row["dfs_entries"]:
            raise ValueError(f"Unaccounted DFS entries: {row['run_id']}")
        if row["winner"] and score["selected_fingerprint"] != row["winner"]["fingerprint"]:
            raise ValueError(f"Publication and evaluator disagree: {row['run_id']}")
        if row["error"] or score["measurement_status"] == "unknown":
            raise ValueError(f"Run evidence incomplete: {row['run_id']}")
        row["validity"] = score["validity"]
        row["quality_score"] = score["quality_score"]
        row["first_valid_seconds"] = score["first_valid_seconds"]
        metrics = selected_scores.get(row["run_id"], {}).get("metrics", {})
        row["transport_minutes"] = metrics.get("transport_minutes")
        row["attraction_minutes"] = metrics.get("attraction_minutes")
        row["route_calls"] = sum(row["branch_routes"].values())
        row["full_validation_calls"] = sum(row["branch_validations"].values())
        row["max_first_time_share"] = concentration(row["branch_seconds"])
        # Use the same initial-order and repetition for both arms.
        key = (row["budget_id"], row["order"], row["repetition"])
        methods = pairs.setdefault(key, {})
        if row["method"] in methods:
            raise ValueError(f"Duplicate run condition: {key}, {row['method']}")
        methods[row["method"]] = row
    comparisons = []
    for (budget, order, repetition), methods in sorted(pairs.items()):
        base, trial = methods["dfs"], methods["round_robin"]
        if base["first_ranked"] != trial["first_ranked"]:
            raise ValueError(f"Initial rankings differ: {budget}, {order}, {repetition}")
        comparisons.append({"budget": budget, "order": order, "repetition": repetition,
                            "penalty_outcome": penalty_outcome(base, trial),
                            "baseline": base["run_id"], "round_robin": trial["run_id"]})
    return comparisons


def concentration(branch_values):
    named = [value for name, value in branch_values.items() if name]
    return max(named) / sum(named) if named and sum(named) else None


def make_budget_curve(rows, plans):
    valid = {(r["run_id"], r["fingerprint"]) for r in plans if r["validation"]["status"] == "valid"}
    result = []
    for row in rows:
        if row["node_budget"]:
            continue
        for cutoff in (1, 3, 5, 10, 15):
            if cutoff > row["wall_budget"]:
                continue
            incumbent = next((p for p in reversed(row["publications"]) if p["at_seconds"] <= cutoff), None)
            accepted = incumbent is not None and (row["run_id"], incumbent["fingerprint"]) in valid
            result.append({
                "run_id": row["run_id"], "method": row["method"], "order": row["order"],
                "repetition": row["repetition"], "budget_id": row["budget_id"],
                "cutoff_seconds": cutoff, "valid": accepted,
                "penalty": incumbent["penalty"] if accepted else None,
            })
    return result


def make_report(rows, comparisons, checks, curve):
    ranked = next((r["first_ranked"] for r in rows if r["order"] == "123" and r["first_ranked"]), [])
    names = "、".join(f"{i} 为 {name}" for i, name in enumerate(ranked, 1))
    lines = [
        "# EXP003 首景点分支轮转",
        "",
        f"同一个冻结状态的六种首三景点顺序。{names}。原软惩罚越低越好。"
        "规则质量分用于辅助观察，人工校准仍不足。",
        "",
    ]
    budgets = sorted({r["budget_id"] for r in rows}, key=lambda key: (key[0], float(key[1:])))
    for budget in budgets:
        group = [r for r in rows if r["budget_id"] == budget]
        outcomes = Counter(p["penalty_outcome"] for p in comparisons if p["budget"] == budget)
        lines += [
            f"## {budget}",
            "",
            f"按合法性和原软惩罚比较，轮转胜 {outcomes['win']}、平 {outcomes['tie']}、"
            f"负 {outcomes['loss']}、双方失败 {outcomes['both_failed']}、未知 {outcomes['unknown']}。",
            "",
            "| 方法 | 独立审核通过 | 首景点数中位 | 最大分支入口占比中位 | DFS入口中位 | "
            "首方案发布秒中位 | 规则质量分中位 | 耗时秒中位 | 路线请求中位 | 完整校验中位 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for method in ("dfs", "round_robin"):
            sample = [r for r in group if r["method"] == method]
            successes = sum(r["validity"] == "valid" for r in sample)
            fields = ("distinct_first_attractions", "max_first_share", "dfs_entries",
                      "first_published_seconds", "quality_score", "elapsed_seconds",
                      "route_calls", "full_validation_calls")
            lines.append(f"| {method} | {successes}/{len(sample)} | " +
                         " | ".join(values_text([typical(sample, f)]) for f in fields) + " |")
        lines += [
            "",
            "| 顺序 | DFS 原惩罚 | 轮转原惩罚 | DFS 最大入口占比 | 轮转最大入口占比 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for order in sorted({r["order"] for r in group}):
            arms = [sorted((r for r in group if r["order"] == order and r["method"] == method),
                           key=lambda r: r["repetition"]) for method in ("dfs", "round_robin")]
            columns = [values_text([r[field] for r in arm])
                       for field in ("best_penalty", "max_first_share") for arm in arms]
            lines.append(f"| {order} | " + " | ".join(columns) + " |")
        lines += ["", "| 方法 | 最大分支搜索时间占比中位 | 市内交通分钟中位 | 游览分钟中位 |",
                  "| --- | ---: | ---: | ---: |"]
        for method in ("dfs", "round_robin"):
            sample = [r for r in group if r["method"] == method]
            fields = ("max_first_time_share", "transport_minutes", "attraction_minutes")
            lines.append(f"| {method} | " + " | ".join(values_text([typical(sample, f)]) for f in fields) + " |")
        lines.append("")
    if curve:
        lines += ["## 秀湖公园排首的发布进度", "",
                  "单元格记录按时发布的独立审核通过方案数，以及原惩罚为 0 的方案数。"
                  "每条曲线来自指定总预算下的实际搜索。", "",
                  "| 总预算 | 检查秒 | DFS 合法 / 0 惩罚 | 轮转合法 / 0 惩罚 |",
                  "| --- | ---: | ---: | ---: |"]
        for budget, cutoff in sorted({(p["budget_id"], p["cutoff_seconds"]) for p in curve},
                                     key=lambda key: (float(key[0][1:]), key[1])):
            cells = []
            for method in ("dfs", "round_robin"):
                points = [p for p in curve if p["budget_id"] == budget and p["cutoff_seconds"] == cutoff
                          and p["method"] == method and p["order"].startswith("3")]
                cells.append(f"{sum(p['valid'] for p in points)} / {sum(p['penalty'] == 0 for p in points)}")
            lines.append(f"| {budget} | {cutoff} | " + " | ".join(cells) + " |")
        lines.append("")
    if checks:
        lines += ["## 对照回放", "",
                  "普通 DFS 的候选序列与旧运行逐指纹、逐惩罚核对。", ""]
        for order, check in checks.items():
            lines.append(f"- {order} 对应 {check['historical_variant']}，"
                         f"序列{'一致' if check['identical_candidate_sequence'] else '有差异'}，"
                         f"{check['new_candidates']} / {check['historical_candidates']} 条。")
        lines.append("")
    lines += [
        "首景点占比的分母为已进入首景点分支的 DFS 入口。"
        "公共前缀、分支发现、校验、评分及在线最优方案落盘均计入墙钟。",
        "",
        "首方案时间的中位数仅使用已有发布记录的运行。失败和未发布的数量见独立审核列。",
        "节点条件按入口配额比较。墙钟条件只接受截止前已写出的方案，原子调用可能造成短暂超时。",
        "",
        "逐运行分支统计、原惩罚发现曲线和选中方案见各运行目录。"
        "独立评分报告见 evaluation 目录，各时刻原惩罚见 analysis.json 的 budget_curve。"
        "本次数据只有一个需求实例，六种顺序和重复运行用于敏感性分析。",
        "",
        "原始行程指标和配对差值见 [分项评估](breakdown/REPORT.md)。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    rows, entries = [], []
    for root in args.runs:
        rows.extend(read_json(root / "comparison.json"))
        entries.extend(read_json(root / "manifest.json")["runs"])
    checks = check_baseline(rows)
    if any(not check["identical_candidate_sequence"] for check in checks.values()):
        raise ValueError("The ordinary DFS no longer matches the historical candidate sequence")
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / "manifest.json", {"schema_version": 1, "runs": entries})
    evaluate(args.out / "manifest.json", args.out / "evaluation", baseline="dfs")
    scored = read_jsonl(args.out / "evaluation/run_metrics.jsonl")
    plans = read_jsonl(args.out / "evaluation/plan_scores.jsonl")
    comparisons = summarize(rows, scored, plans)
    curve = make_budget_curve(rows, plans)
    write_json(args.out / "analysis.json", {"runs": rows, "pairs": comparisons,
                                           "baseline_replay": checks, "budget_curve": curve})
    (args.out / "REPORT.md").write_text(make_report(rows, comparisons, checks, curve), encoding="utf-8")
    write_breakdown(args.out, args.out / "breakdown")
    print(args.out / "REPORT.md")


if __name__ == "__main__":
    main()
