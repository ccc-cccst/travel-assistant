"""Audit and report a suite of paired first-attraction budget experiments."""

import argparse
from collections import Counter
from pathlib import Path
from statistics import median

from .analyze import concentration, penalty_outcome
from .breakdown import plan_details
from .offline_inputs import HARD
from experiments.evaluation.adapters import import_manifest, publication_state
from experiments.evaluation.common import HERE, file_digest, plan_fingerprint, read_json, write_json
from experiments.evaluation.run import Evaluator


METRICS = {
    "max_first_share": ("最大分支入口占比", 1),
    "max_first_time_share": ("最大分支活跃时间占比", 1),
    "distinct_first_attractions": ("覆盖首景点", 1),
    "route_calls": ("路线请求", 1),
    "candidate_count": ("候选行程", 1),
    "first_valid_seconds": ("首次独立审核通过方案的发布时间", 2),
    "elapsed_seconds": ("搜索结束耗时", 2),
    "transport_minutes": ("市内交通分钟", 1),
    "walking_minutes": ("步行分钟", 1),
    "attraction_minutes": ("游览分钟", 1),
    "unique_attractions": ("不同景点数", 1),
    "total_cost": ("记录总费用（元）", 2),
    "idle_minutes": ("规则计入的可用空档（分钟）", 1),
    "T": ("交通规则分", 2),
    "R": ("节奏规则分", 2),
    "H": ("住宿规则分", 2),
    "U": ("重复游览规则分", 2),
}


def audit(row, run, evaluator):
    state = publication_state(run)
    if state["measurement_status"] != "known" or row["error"]:
        raise ValueError(f"Incomplete evidence for {run['case_id']} / {run['run_id']}")
    if sum(row["branch_entries"].values()) != row["dfs_entries"]:
        raise ValueError(f"Unaccounted DFS entries in {run['run_id']}")
    recorded = (row["winner"] or {}).get("fingerprint")
    published = plan_fingerprint(state["selected_plan"]) if state["selected_plan"] else None
    if recorded != published:
        raise ValueError(f"Conflicting winner for {run['run_id']}")
    original_penalties = {p["fingerprint"]: p["penalty"] for p in row["publications"]}
    row["route_calls"] = sum(row["branch_routes"].values())
    row["max_first_time_share"] = concentration(row["branch_seconds"])
    row["by_cutoff"] = {}
    for cutoff in (5, row["wall_budget"]):
        point = {"validity": "no_result", "first_valid_seconds": None, "best_penalty": None}
        for publication in state["publications"]:
            if publication["at_seconds"] > cutoff:
                break
            result = evaluator.score(publication["plan"], run)
            status = result["validation"]["status"]
            point.update(validity=status, fingerprint=publication["fingerprint"],
                         published_at=publication["at_seconds"],
                         best_penalty=original_penalties[publication["fingerprint"]],
                         validation=result["validation"], metrics={})
            if status != "valid":
                continue
            if point["first_valid_seconds"] is None:
                point["first_valid_seconds"] = publication["at_seconds"]
            scores = result["scores"]
            point["metrics"] = {key: scores["metrics"].get(key) for key in METRICS}
            point["metrics"].update(plan_details(publication["plan"]))
            idle = [day["idle_minutes"] for day in scores["metrics"]["days"]]
            point["metrics"]["idle_minutes"] = sum(idle) if all(value is not None for value in idle) else None
            for key, component in scores["components"].items():
                point["metrics"][key] = component["score"] if component["status"] == "known" else None
            point["preference"] = scores.get("preferences")
        row["by_cutoff"][str(int(cutoff))] = point
    return row


def pair_case(directory, evaluator):
    runs = {run["run_id"]: run for run in import_manifest(directory / "manifest.json")}
    pairs = {}
    for row in read_json(directory / "comparison.json"):
        key = (row["order"], row["repetition"])
        methods = pairs.setdefault(key, {})
        if row["method"] in methods:
            raise ValueError(f"Duplicate method in {directory} / {key}")
        methods[row["method"]] = audit(row, runs[row["run_id"]], evaluator)
    comparisons = []
    for (order, repetition), methods in sorted(pairs.items()):
        baseline, trial = methods["dfs"], methods["round_robin"]
        if baseline["first_ranked"] != trial["first_ranked"]:
            raise ValueError(f"Rankings disagree for {directory} / {order}")
        comparisons.append({"case": directory.name, "order": order, "repetition": repetition,
                            "directory": str(directory), "dfs": baseline, "round_robin": trial})
    return comparisons


def display(value, precision=2):
    return "—" if value is None else f"{value:.{precision}f}"


def median_pair_delta(pairs, key, cutoff):
    deltas = []
    for pair in pairs:
        values = []
        for method in ("dfs", "round_robin"):
            row = pair[method]
            point = row["by_cutoff"][cutoff]
            value = row.get(key) if key in row else point.get(key, point.get("metrics", {}).get(key))
            if value is not None and key.endswith("_share"):
                value *= 100
            values.append(value)
        if all(value is not None for value in values):
            deltas.append(values[1] - values[0])
    return (median(deltas) if deltas else None, len(deltas),
            sum(v < -1e-8 for v in deltas), sum(v > 1e-8 for v in deltas))


def outcomes(pairs, cutoff):
    counts = Counter()
    for pair in pairs:
        base, trial = (pair[m]["by_cutoff"][cutoff] for m in ("dfs", "round_robin"))
        counts["dfs_valid"] += base["validity"] == "valid"
        counts["rr_valid"] += trial["validity"] == "valid"
        counts["lost_valid"] += base["validity"] == "valid" and trial["validity"] != "valid"
        counts["gained_valid"] += trial["validity"] == "valid" and base["validity"] != "valid"
        counts[penalty_outcome(base, trial)] += 1
    return counts


def report(pairs, preparation, cutoff):
    regular = [p for p in pairs if p["case"] in HARD]
    reference = [p for p in pairs if p["case"] not in HARD]
    natural = [p for p in regular if p["order"] == "123"]
    stress = [p for p in regular if p["order"] != "123"]
    lines = ["# EXP005 跨需求首景点预算", "",
             "本地基准硬约束与推荐回退为机制验证组。已有 Qwen 冻结快照单列。"
             "各次最多 20 个合法候选。独立复查各截止时点已发布的方案。", "",
             f"目标 {len(HARD)} 条独立需求，冻结 {sum(p['status'] == 'frozen' for p in preparation.values())} 条，"
             f"已完成默认顺序的配对 {len(natural)} 条。", "",
             "| 组别 | 截止（秒） | 配对 | 普通合法 | 轮转合法 | 丢失合法 | 新增合法 | 胜 / 平 / 负 / 均无合法 / 未知 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for label, sample in (("默认顺序", natural), ("提前第 2 或 3 位", stress),
                          ("Qwen 冻结参考", reference)):
        for point in ("5", cutoff):
            c = outcomes(sample, point)
            marks = " / ".join(str(c[k]) for k in ("win", "tie", "loss", "both_failed", "unknown"))
            lines.append(f"| {label} | {point} | {len(sample)} | {c['dfs_valid']} | {c['rr_valid']} | "
                         f"{c['lost_valid']} | {c['gained_valid']} | {marks} |")
    for label, sample in (("默认顺序", natural), ("顺序干预", stress)):
        lines += ["", f"## {label}的分项变化", "",
                  f"以下为 {cutoff} 秒最终配对差值（轮转减普通）。分支占比使用百分点。", "",
                  "| 指标 | 差值中位数 | 下降 / 上升 | 有值配对 |",
                  "| --- | ---: | --- | ---: |"]
        for key, (name, precision) in METRICS.items():
            delta, count, less, more = median_pair_delta(sample, key, cutoff)
            lines.append(f"| {name} | {display(delta, precision)} | {less} / {more} | {count}/{len(sample)} |")
    lines += ["", "## 逐需求默认顺序", "",
              "| 需求 | 合法性（普通 → 轮转） | 原惩罚（普通 → 轮转） | 最大分支入口占比 | 首次合法发布（秒） |",
              "| --- | --- | --- | --- | --- |"]
    for pair in natural + [p for p in reference if p["order"] == "123"]:
        base, trial = (pair[m]["by_cutoff"][cutoff] for m in ("dfs", "round_robin"))
        share = [p["max_first_share"] for p in (pair["dfs"], pair["round_robin"])]
        shares = " → ".join("—" if value is None else f"{value:.0%}" for value in share)
        lines.append(f"| {pair['case']} | {base['validity']} → {trial['validity']} | "
                     f"{display(base['best_penalty'])} → {display(trial['best_penalty'])} | {shares} | "
                     f"{display(base['first_valid_seconds'])} → {display(trial['first_valid_seconds'])} |")
    lines += ["", "## 逐需求顺序干预", "",
              "| 需求 | 普通合法 / 轮转合法 | 胜 / 平 / 负 / 均无合法 / 未知 |",
              "| --- | --- | --- |"]
    for case in sorted({p["case"] for p in stress}):
        cases = [p for p in stress if p["case"] == case]
        c = outcomes(cases, cutoff)
        marks = " / ".join(str(c[k]) for k in ("win", "tie", "loss", "both_failed", "unknown"))
        lines.append(f"| {case} | {c['dfs_valid']}/{len(cases)} → {c['rr_valid']}/{len(cases)} | {marks} |")
    lines += ["", "未通过独立审核的发布也保留原惩罚供排查，胜负按独立审核优先。"
              "干预顺序配对共享同一需求，计数用于描述压力测试。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replace-pairs-from", type=Path, nargs="*", default=[],
                        help="Use rechecks of interrupted pairs. Each directory must use the original case name.")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    evaluator = Evaluator(read_json(HERE / "config.v0.json"), cache_dir=args.out / "judge_cache")
    roots = sorted(p.parent for p in (args.root / "runs").glob("*/comparison.json"))
    pairs = [pair for root in roots for pair in pair_case(root, evaluator)]
    replacements = [pair for root in args.replace_pairs_from for pair in pair_case(root, evaluator)]
    pairs_by_key = {(p["case"], p["order"], p["repetition"]): p for p in pairs}
    for replacement in replacements:
        key = (replacement["case"], replacement["order"], replacement["repetition"])
        original = pairs_by_key[key]
        original_config = read_json(Path(original["directory"]) / "config.json")
        replacement_config = read_json(Path(replacement["directory"]) / "config.json")
        for field in ("stage", "quantum", "initial_width", "ranking", "wall_seconds",
                      "candidate_limit", "frozen_sha256"):
            if replacement_config[field] != original_config[field]:
                raise ValueError(f"Replacement changes {field} for {key}")
        replacement["replaces"] = original["directory"]
        pairs_by_key[key] = replacement
    pairs = list(pairs_by_key.values())
    preparation = read_json(args.root / "offline_inputs/preparation.json")
    budgets = {p["dfs"]["wall_budget"] for p in pairs}
    if len(budgets) != 1 or not pairs:
        raise ValueError("Expected one wall budget across the paired cases")
    cutoff = str(int(budgets.pop()))
    write_json(args.out / "analysis.json", {
        "pairs": pairs, "preparation": preparation, "max_cutoff_seconds": int(cutoff),
        "comparison_sha256": {p.name: file_digest(p / "comparison.json") for p in roots},
        "replacement_comparison_sha256": {
            str(p): file_digest(p / "comparison.json") for p in args.replace_pairs_from},
        "evaluation_model_requests": evaluator.judge_request_count,
    })
    content = report(pairs, preparation, cutoff)
    if replacements:
        changed = "、".join(f"{p['case']} {p['order']}" for p in replacements)
        content += f"\n使用单独重跑的数据替换以下计时中断的配对。{changed}。原始运行目录仍保留。\n"
    (args.out / "REPORT.md").write_text(content, encoding="utf-8")
    print(args.out / "REPORT.md")


if __name__ == "__main__":
    main()
