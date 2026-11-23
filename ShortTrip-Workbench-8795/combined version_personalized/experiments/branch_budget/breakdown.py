"""Show measured changes in matched EXP003 runs without combining the metrics."""

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from experiments.evaluation.common import number, plan_fingerprint, read_json, read_jsonl
from experiments.evaluation.features import _clock


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    precision: int = 0
    clock: bool = False


SEARCH = (
    Metric("distinct_first_attractions", "覆盖首景点（个）", 1),
    Metric("max_first_share", "最大分支入口占比（%）", 1),
    Metric("max_first_time_share", "最大分支活跃时间占比（%）", 1),
    Metric("dfs_entries", "DFS 入口（次）", 1),
    Metric("route_calls", "路线请求（次）", 1),
    Metric("full_validation_calls", "完整校验（次）", 1),
    Metric("candidate_count", "候选行程（条）", 1),
    Metric("first_published_seconds", "首份方案发布时间（秒）", 3),
    Metric("elapsed_seconds", "搜索和发布耗时（秒）", 3),
)
ITINERARY = (
    Metric("transport_minutes", "市内交通（分钟）"),
    Metric("walking_minutes", "步行（分钟）"),
    Metric("walking_distance_km", "步行（公里）", 2),
    Metric("attraction_minutes", "游览（分钟）"),
    Metric("unique_attractions", "不同景点（个）"),
    Metric("first_day_sightseeing", "首日开始游览", clock=True),
    Metric("first_day_last_visit_end", "首日最后一次游览结束", clock=True),
    Metric("first_day_hotel", "首日到达住宿", clock=True),
    Metric("idle_minutes", "规则计入的可用空档（分钟）"),
    Metric("meal_minutes", "用餐（分钟）"),
    Metric("hotel_switches", "换酒店（次）"),
    Metric("nonessential_revisits", "非必要重访（次）"),
    Metric("ticket_cost", "景点费用（元）", 2),
    Metric("total_cost", "记录总费用（元）", 2),
)
COMPONENTS = (
    Metric("T", "交通规则分", 2),
    Metric("R", "节奏规则分", 2),
    Metric("H", "住宿规则分", 2),
    Metric("U", "景点重复规则分", 2),
)


def published_plan(source, entry, fingerprint):
    plan = read_json(source / entry["selected_plan"])
    if plan_fingerprint(plan) == fingerprint:
        return plan
    # The final file can include a publication that completed after the cutoff.
    for candidate in read_json(source / entry["candidates"]):
        if candidate["fingerprint"] == fingerprint:
            plan = candidate["plan"]
            if plan_fingerprint(plan) == fingerprint:
                return plan
    raise ValueError(f"Published plan missing: {entry['run_id']}")


def plan_details(plan):
    first_day = next((day["activities"] for day in plan["itinerary"] if day["day"] == 1), [])
    visits = [activity for activity in first_day if activity["type"] == "attraction"]
    stays = [activity for activity in first_day if activity["type"] == "accommodation"]
    costs = [activity.get("cost") for day in plan["itinerary"] for activity in day["activities"]
             if activity["type"] == "attraction"]
    return {
        "first_day_sightseeing": min((_clock(visit["start_time"]) for visit in visits), default=None),
        "first_day_last_visit_end": max((_clock(visit["end_time"]) for visit in visits), default=None),
        "first_day_hotel": min((_clock(stay["start_time"]) for stay in stays), default=None),
        "ticket_cost": sum(costs) if all(number(cost) for cost in costs) else None,
    }


def collect(source, data):
    entries = {entry["run_id"]: entry for entry in read_json(source / "manifest.json")["runs"]}
    selected = {row["run_id"]: row for row in read_jsonl(source / "evaluation/plan_scores.jsonl")
                if row["role"] == "selected"}
    results = {}
    for run in data["runs"]:
        key = run["run_id"]
        metrics = {metric.key: run.get(metric.key) for metric in SEARCH}
        for share in ("max_first_share", "max_first_time_share"):
            if number(metrics[share]):
                metrics[share] *= 100
        results[key] = metrics
        if run["validity"] != "valid":
            continue
        score = selected[key]
        fingerprint = run["winner"]["fingerprint"]
        if score["fingerprint"] != fingerprint or score["validation"]["status"] != "valid":
            raise ValueError(f"Selected plan differs from audited publication: {key}")
        scores = score["scores"]
        facts = scores["metrics"]
        metrics.update({metric.key: facts.get(metric.key) for metric in ITINERARY})
        metrics.update(plan_details(published_plan(source, entries[key], fingerprint)))
        idle = [day.get("idle_minutes") for day in facts["days"]]
        metrics["idle_minutes"] = sum(idle) if all(number(value) for value in idle) else None
        for metric in COMPONENTS:
            component = scores["components"][metric.key]
            metrics[metric.key] = component["score"] if component["status"] == "known" else None
    return results


def fmt(metric, value):
    if metric.clock:
        hours, minutes = divmod(round(value), 60)
        return f"{hours:02}:{minutes:02}"
    precision = metric.precision or (1 if value % 1 else 0)
    return f"{value:,.{precision}f}"


def comparison_table(pairs, metrics, values):
    lines = [
        "| 指标 | 普通 DFS 中位 | 轮转 DFS 中位 | 配对差值中位 | 增 / 同 / 减 | 可比对数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for metric in metrics:
        observations = [(values[pair["baseline"]].get(metric.key), values[pair["round_robin"]].get(metric.key))
                        for pair in pairs]
        known = [(left, right) for left, right in observations if number(left) and number(right)]
        if not known:
            lines.append(f"| {metric.label} | — | — | — | — | 0/{len(pairs)} |")
            continue
        baseline, trial = zip(*known)
        deltas = [right - left for left, right in known]
        delta = median(deltas)
        precision = metric.precision or (1 if delta % 1 else 0)
        unit = " 分钟" if metric.clock else " 个百分点" if metric.key.endswith("_share") else ""
        rounded = [round(value, metric.precision) for value in deltas]
        counts = (sum(d > 0 for d in rounded), rounded.count(0), sum(d < 0 for d in rounded))
        counts_text = " / ".join(map(str, counts))
        delta_text = f"{delta:+,.{precision}f}" if round(delta, precision) else "0"
        lines.append(f"| {metric.label} | {fmt(metric, median(baseline))} | {fmt(metric, median(trial))} | "
                     f"{delta_text}{unit} | {counts_text} | {len(known)}/{len(pairs)} |")
    return lines


def write_pairs(path, pairs, runs, values):
    keys = ("budget", "order", "repetition", "baseline", "round_robin")
    columns = list(keys) + ["dfs_validity", "round_robin_validity"]
    metrics = SEARCH + ITINERARY + COMPONENTS
    columns.extend(f"{metric.key}_{suffix}" for metric in metrics
                   for suffix in ("dfs", "round_robin", "delta"))
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for pair in pairs:
            row = {key: pair[key] for key in keys}
            row.update(dfs_validity=runs[pair["baseline"]]["validity"],
                       round_robin_validity=runs[pair["round_robin"]]["validity"])
            for metric in metrics:
                left = values[pair["baseline"]].get(metric.key)
                right = values[pair["round_robin"]].get(metric.key)
                row[f"{metric.key}_dfs"] = left
                row[f"{metric.key}_round_robin"] = right
                row[f"{metric.key}_delta"] = round(right - left, 9) if number(left) and number(right) else None
            writer.writerow(row)


def write_breakdown(source, out):
    data = read_json(source / "analysis.json")
    values = collect(source, data)
    runs = {row["run_id"]: row for row in data["runs"]}
    budgets = sorted({pair["budget"] for pair in data["pairs"]}, key=lambda value: (value[0], float(value[1:])))
    third = next((row["first_ranked"][0] for row in runs.values()
                  if row["order"].startswith("3") and row["first_ranked"]), "原排序第三景点")
    lines = [
        "# EXP003 分项评估",
        "",
        f"基于已保存的 {len(data['pairs'])} 对搜索。每对固定预算、首三景点顺序和重复编号。"
        "行程指标来自截止时已发布且通过独立审核的选中方案。六种顺序和重复运行均来自同一个冻结需求。",
        "",
        "差值按每对先做轮转减普通 DFS，再取中位数。"
        "增、同、减使用各指标的展示精度判定，表示数值方向。时刻差值为分钟，占比差值为百分点。",
        "",
        f"行程表单列 {third} 排首的顺序与其余顺序，便于查看初始顺序对行程的影响。"
        "规则分沿用实验评估配置。体验和偏好的人工结论仍待验证。",
        "",
    ]
    for budget in budgets:
        pairs = [pair for pair in data["pairs"] if pair["budget"] == budget]
        successes = [sum(runs[pair[arm]]["validity"] == "valid" for pair in pairs)
                     for arm in ("baseline", "round_robin")]
        name = f"{budget[1:]} 次 DFS 入口" if budget.startswith("n") else f"{budget[1:]} 秒"
        lines += [f"## {name}", "",
                  f"普通 DFS 有 {successes[0]}/{len(pairs)} 份方案通过独立审核。"
                  f"轮转有 {successes[1]}/{len(pairs)} 份通过。",
                  "", "### 搜索覆盖与开销", "", *comparison_table(pairs, SEARCH, values), ""]
        for title, starts_third in ((f"{third}排首", True), ("其余首景点顺序", False)):
            cohort = [pair for pair in pairs if pair["order"].startswith("3") == starts_third]
            if cohort:
                lines += [f"### {title}的行程变化", "",
                          *comparison_table(cohort, ITINERARY + COMPONENTS, values), ""]
    lines += [
        "pairs.csv 保留每对的原始数值、差值和两个运行编号。时刻以当天零点起算的分钟数记录。"
        "可用空档按评估器的可用时段和活动占用计算，适合与节奏规则分一起阅读。",
        "",
    ]
    out.mkdir(parents=True, exist_ok=False)
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    write_pairs(out / "pairs.csv", data["pairs"], runs, values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    write_breakdown(args.analysis, args.out)
    print(args.out / "REPORT.md")


if __name__ == "__main__":
    main()
