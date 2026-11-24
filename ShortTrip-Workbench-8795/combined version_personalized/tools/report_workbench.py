"""Audit workbench outputs against the frozen P036 constraints."""

import argparse
from collections import Counter
from math import ceil
from pathlib import Path
from statistics import median
import json

from acceptance_p036 import OUT, read, save
from report_p036 import check_raw_plans


def latency(rows, key):
    values = sorted(row[key] for row in rows if isinstance(row.get(key), (int, float)))
    return {"count": len(values), "p50": median(values) if values else None,
            "p90": values[ceil(.9 * len(values)) - 1] if values else None}


def breakdown(rows):
    complete = [row for row in rows if row["outcome"] == "complete"]
    return {"submitted": len(rows), "outcomes": dict(Counter(row["outcome"] for row in rows)),
            "direct": len(complete), "has_alternative": sum(row["alternatives"] > 0 for row in complete),
            "interpret_s": latency(rows, "interpret_s"), "first_s": latency(complete, "first_s"),
            "final_s": latency(complete, "final_s")}


def report(run):
    rows = read(run / "SUMMARY.json")
    manifest = read(OUT / "FROZEN30.json")
    if {r["case"] for r in rows} != {c["case"] for c in manifest["cases"]} or len(rows) != len(manifest["cases"]):
        raise ValueError("回归尚未覆盖完整的 30 条冻结需求。")
    def selected_plans():
        for row in rows:
            if row["outcome"] != "complete":
                continue
            result = read(run / row["case"] / "RESULT.json")
            yield row["case"], next(v["plan"] for v in result["group"]["versions"] if v["id"] == row["candidate"])
    audit = check_raw_plans(selected_plans(), manifest)
    save(run / "RAW_CHECKS.json", audit)
    summary = {"total": breakdown(rows), "levels": {
        level: breakdown([row for row in rows if row["level"] == level])
        for level in ("easy", "medium", "human")
    }, "first_submission_only": True, "raw_checks": audit["checks"], "raw_failures": audit["failed"],
       "unverified_published": [{"case": row["case"], "candidates": row["verification_failures_shown"]}
                                for row in rows if row.get("verification_failures_shown")],
       "manual_checks": dict(Counter(item for row in rows for item in row.get("pending", []))),
       "failed_delivery": [{key: row.get(key) for key in ("case", "outcome", "reason", "issues", "job")}
                           for row in rows if row["outcome"] != "complete"],
       "timeouts": [row["case"] for row in rows if row.get("planner_state") == "search_timeout"],
       "poll_errors": [{row["case"]: row["poll_errors"]} for row in rows if row.get("poll_errors")]}
    return save(run / "METRICS.json", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    print(json.dumps(report(parser.parse_args().run), ensure_ascii=False, indent=2))
