"""Summarize exported comparison study records by display and within participant."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median


def seconds(values):
    return round(median(values) / 1000, 3) if values else None


def summarize(records):
    groups = {mode: [row for row in records if row["display"] == mode] for mode in ("table", "summary")}
    by_mode = {}
    for mode, rows in groups.items():
        finished = [row for row in rows if row["status"] == "complete"]
        graded = [row for row in finished if row["correctBenefit"] is not None and row["correctTradeoff"] is not None]
        timed = [row for row in finished if row["firstChoiceMilliseconds"] is not None]
        by_mode[mode] = {
            "tasks": len(rows),
            "completed": len(finished),
            "completed_without_help": sum(row["help"] == 0 for row in finished),
            "correct_choice": sum(row["correctChoice"] is True for row in finished),
            "reason_graded": len(graded),
            "advantage_and_tradeoff_correct": sum(row["correctBenefit"] and row["correctTradeoff"] for row in graded),
            "first_choice_seconds_median": seconds([row["firstChoiceMilliseconds"] for row in timed]),
            "submitted_seconds_median": seconds([row["decisionMilliseconds"] for row in finished]),
            "load_seconds_median": seconds([row["loadMilliseconds"] for row in rows if row["loadMilliseconds"] is not None]),
            "help": sum(row["help"] for row in rows),
            "manual_checks": sum(row["manualChecks"] for row in rows),
        }
    paired = defaultdict(dict)
    for row in records:
        if row["status"] == "complete" and row["help"] == 0 and row["firstChoiceMilliseconds"] is not None:
            paired[row["participant"]][row["display"]] = row
    differences = []
    percentages = []
    for arms in paired.values():
        if set(arms) != set(groups) or arms["summary"]["scenario"] == arms["table"]["scenario"]:
            continue
        baseline = arms["table"]["firstChoiceMilliseconds"]
        with_summary = arms["summary"]["firstChoiceMilliseconds"]
        differences.append(baseline - with_summary)
        if baseline:
            percentages.append(100 * (baseline - with_summary) / baseline)
    return {
        "participants": len({row["participant"] for row in records}),
        "by_display": by_mode,
        "paired_unaided": {
            "participants": len(differences),
            "seconds_saved_median": seconds(differences),
            "percent_saved_median": round(median(percentages), 1) if percentages else None,
            "summary_faster": sum(value > 0 for value in differences),
            "equal": differences.count(0),
            "table_faster": sum(value < 0 for value in differences),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--out", type=Path)
    options = parser.parse_args()
    data = json.loads(options.export.read_text(encoding="utf-8-sig"))
    if data.get("schema") != "shorttrip_comparison_study_v1":
        parser.error("需要使用方案比较工作台导出的记录。")
    result = summarize(data["records"])
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if options.out:
        options.out.write_text(output, encoding="utf-8")
    print(output)
