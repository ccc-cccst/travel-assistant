"""Report stage attribution and quality for live/historical speed observations."""

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from statistics import median

from live_report import MODULES, PHASE_NAMES, jsonl, profile, read
from speed_study import ARTIFACTS, PILOT, PAIRS
from experiments.evaluation.common import plan_fingerprint
from experiments.evaluation.features import score_plan


HISTORY = (
    ("EXP008", ARTIFACTS / "EXP008_1794455588801"),
    ("EXP008", ARTIFACTS / "EXP008_all_tiers_1794457018569/run"),
    ("EXP010", PILOT),
)
METRICS = {"total_cost": "费用元", "transport_minutes": "市内交通分钟",
           "unique_attractions": "不同景点数", "nonessential_revisits": "非必要重访数",
           "hotel_switches": "换酒店次数"}


def details(batch, variant, case, arm, directory, query, stats):
    folder = directory / "profile"
    requests = jsonl(folder / "requests.jsonl")
    if len(requests) != stats["request_count"]:
        raise ValueError(f"Inconsistent request ledger in {folder}")
    ranking = [r for r in requests if "ranking_chars" in r]
    seen, repeats = set(), []
    for request in requests:
        key = request.get("request_sha256")
        if key is None or request["status"] != "complete":
            continue
        if key in seen:
            repeats.append(request)
        seen.add(key)
    dfs = stats["stats"].get("dfs", {}).get("calls", 0)
    completed = [r for r in requests if r["status"] == "complete"]
    slowest = max(stats["phase_requests"], key=lambda phase: stats["phase_requests"][phase]["seconds"], default=None)
    selected_audit = stats["selected_audit"]
    row = {
        "batch": batch, "variant": variant, "case": case, "arm": arm, "uid": query["uid"],
        "tier": query["tag"], "days": query["days"], "people": query["people_number"],
        "source_city": query["start_city"], "target_city": query["target_city"],
        "seconds": stats["wall_seconds"], "llm": stats["llm_seconds"], "stop": stats["stop_reason"],
        "budget": stats["budget_seconds"], "dfs_calls": dfs, "dfs_seconds_inclusive": stats["dfs_inclusive_seconds"],
        "first_seconds": stats["first_published_seconds"] if stats["first_audit"]["common"] == "pass" else None,
        "common_audit": selected_audit["common"], "c_audit": selected_audit["C_audit"],
        "candidates": stats["candidate_count"], "request_count": len(requests),
        "generated_tokens": sum(r["output_tokens"] for r in completed),
        "output_cap_hits": sum(r["output_tokens"] >= r["max_tokens"] for r in completed),
        "ranking_calls_with_format_sample": len(ranking),
        "ranking_missing_list": sum(not r["has_ranking_list"] for r in ranking),
        "duplicate_requests": len(repeats) if any("request_sha256" in r for r in requests) else None,
        "duplicate_seconds": sum(r["seconds"] for r in repeats) if any("request_sha256" in r for r in requests) else None,
        "transport_llm_seconds": sum(r["seconds"] for r in requests if r["phase"] in {"outbound_ranking", "return_ranking"}),
        "transport_llm_calls": sum(r["phase"] in {"outbound_ranking", "return_ranking"} for r in requests),
        "slowest_phase": slowest, "slowest_seconds": stats["phase_requests"].get(slowest, {}).get("seconds", 0),
        "modules": stats["module_seconds"], "phases": stats["phase_requests"],
        "generation": stats["server_seconds"]["generation"],
        "prefill": stats["server_seconds"]["prompt_evaluation"],
    }
    if row["common_audit"] == "pass" and row["c_audit"] != "fail":
        plan = read(folder / "plan.json")
        score = score_plan(plan, query)
        row["quality"] = {key: score["metrics"].get(key) for key in METRICS}
        row["quality"].update({key: score["components"][key]["score"] for key in ("T", "R", "H", "U")})
        row["plan_fingerprint"] = plan_fingerprint(plan)
    return row


def history_rows():
    for label, root in HISTORY:
        report_data = read(root / "analysis/ANALYSIS.json")
        for case in read(root / "manifest.json")["cases"]:
            query = read(root / "queries" / f"{case}.json")
            for arm, row in report_data["runs"][case].items():
                yield details(label, "observed", case, arm, root / case / arm, query, row)


def fmt(value):
    return f"{value:.2f}" if isinstance(value, float) else str(value) if value is not None else "—"


def table(headers, rows):
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |",
            *("| " + " | ".join(map(fmt, row)) + " |" for row in rows), ""]


def report(rows, status):
    historical = [r for r in rows if r["batch"] != "EXP011"]
    fresh = [r for r in rows if r["batch"] == "EXP011" and r["variant"] == "observed"]
    pilot = {(r["case"], r["variant"]): r for r in rows if r["batch"] == "EXP011" and r["variant"] != "observed"}
    doc = ["# EXP011 ShortTrip 速度实验", "",
           f"新增完成 {len(status['finished'])} 次，失败 {len(status['failed'])} 次。"
           f"已有 EXP008/010 完整计时 {len(historical)} 次、{len({r['uid'] for r in historical})} 个需求。"
           "已有记录包含重复需求、两版算法及 360、1800 秒两种截止。下表按批次分列。", "",
           "## 分模块耗时", ""]
    group = defaultdict(list)
    for row in historical + fresh:
        group[row["batch"], row["arm"]].append(row)
    totals = []
    for (batch, arm), cohort in group.items():
        wall = sum(r["seconds"] for r in cohort)
        llm = sum(r["llm"] for r in cohort)
        totals.append((batch, arm, len(cohort), median(r["seconds"] for r in cohort), wall, llm,
                       100 * llm / wall, sum(r["dfs_calls"] == 0 for r in cohort),
                       sum(r["stop"] == "matched_wall_budget" for r in cohort),
                       sum(r["common_audit"] == "pass" for r in cohort)))
    doc += table(["批次", "版本", "次数", "总秒中位数", "总秒合计", "模型秒", "模型占比 %",
                  "未进 DFS", "外层截止", "选中方案共同审核通过"], totals)
    doc += table(["模块独占秒", *[f"{b} {a}" for b, a in group]],
                 ((label, *(sum(r["modules"][label] for r in cohort) for cohort in group.values()))
                  for label in MODULES))
    doc += ["## 慢例明细", "",
            "外层截止属于按固定时间观察的结果。DFS 列是递归入口次数，模型慢点按单次运行中各用途累计时长确定。", ""]
    doc += table(["批次", "需求", "版本", "总秒", "停止", "DFS 入口", "最慢模型阶段", "该阶段秒",
                  "达到输出上限", "候选", "首份共同审核秒"],
                 ((r["batch"], r["case"], r["arm"], r["seconds"], r["stop"], r["dfs_calls"],
                   PHASE_NAMES.get(r["slowest_phase"], "—"), r["slowest_seconds"], r["output_cap_hits"],
                   r["candidates"], r["first_seconds"]) for r in historical + fresh))
    doc += ["## 精简排序输出配对", "",
            "只调整排序提示的输出要求。列表缺失按返回文本格式统计。独立审核对硬约束和原 C 规则分别判定。", ""]
    doc += table(["案例", "组别", "总秒", "模型秒", "输出 tokens", "达到上限", "缺列表 / 已返回",
                  "首份共同审核秒", "候选数", "选中共同审核", "选中 C 审核"],
                 ((case, variant, r["seconds"], r["llm"], r["generated_tokens"], r["output_cap_hits"],
                   f"{r['ranking_missing_list']}/{r['ranking_calls_with_format_sample']}", r["first_seconds"],
                   r["candidates"], r["common_audit"], r["c_audit"])
                  for case in PAIRS for variant in ("baseline", "concise") if (r := pilot.get((case, variant)))))
    doc += table(["案例", "组别", *METRICS.values(), "交通分", "节奏分", "住宿分", "重访分"],
                 ((case, variant, *(r["quality"].get(key) for key in (*METRICS, "T", "R", "H", "U")))
                  for (case, variant), r in pilot.items() if "quality" in r))
    doc += ["语义偏好和体验需要另行盲审。规则维度使用现有 fast_rules_v0。", "",
            "## 城际排序请求成本", ""]
    doc += table(["批次", "ShortTrip 案例", "变体", "去返程调用", "历史实测模型秒", "占实测总秒 %"],
                 ((r["batch"], r["case"], r["variant"], r["transport_llm_calls"], r["transport_llm_seconds"],
                   100 * r["transport_llm_seconds"] / r["seconds"]) for r in rows if r["arm"] == "shorttrip"))
    doc += ["该列为已观测请求成本。Qwen 成功解析的排序会影响后续搜索。", ""]
    if status["failed"]:
        doc += ["## 运行错误", "", *(f"- {r['case']} / {r['variant']} — {r['error']}" for r in status["failed"]), ""]
    return "\n".join(doc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    status = read(args.run / "STATUS.json")
    rows = list(history_rows())
    for item in status["finished"]:
        case, variant = item["case"], item["variant"]
        directory = args.run / variant / case / "shorttrip"
        query = read(args.run / "queries" / f"{case}.json")
        rows.append(details("EXP011", variant, case, "shorttrip", directory, query, profile(directory, query)))
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "REPORT.md").write_text(report(rows, status), "utf-8")
    (args.out / "ANALYSIS.json").write_text(json.dumps({"runs": rows, "status": status}, ensure_ascii=False, indent=2), "utf-8")
    fields = [k for k in rows[0] if k not in ("phases", "modules")]
    with (args.out / "runs.csv").open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(args.out / "REPORT.md")


if __name__ == "__main__":
    main()
