"""Matched comparisons with explicit failures, unknowns and query-level aggregation."""
from collections import Counter, defaultdict
import random
import statistics

from .common import digest, number


def cohort_key(row):
    return digest({key: row.get(key) for key in
                   ("protocol", "scope", "budget", "profile", "evaluation_identity", "activity_policy")})


def match_key(row):
    keys = ("query_sha256", "resource_sha256", "requirements_sha256", "protocol", "scope", "budget",
            "profile", "evaluation_identity", "activity_policy", "repetition", "seed", "order_id")
    return digest({key: row.get(key) for key in keys})


def outcome(new, baseline, margin=3):
    if new.get("protocol") == "published_v1" and (
            new.get("measurement_status") != "known" or baseline.get("measurement_status") != "known"):
        return {"outcome": "unresolved", "reason": "publication_evidence_unknown"}
    a, b = new["validity"], baseline["validity"]
    if "unknown" in (a, b):
        return {"outcome": "unresolved", "reason": "validation_unknown"}
    failures = {"invalid", "no_result"}
    if a in failures and b in failures:
        return {"outcome": "both_failed", "reason": "neither_plan_valid"}
    if a == "valid" and b in failures:
        return {"outcome": "win", "reason": "only_new_valid"}
    if b == "valid" and a in failures:
        return {"outcome": "loss", "reason": "only_baseline_valid"}
    x, y = new.get("composite_score"), baseline.get("composite_score")
    differences = {key+"_difference": new[key+"_score"]-baseline[key+"_score"]
                   if number(new.get(key+"_score")) and number(baseline.get(key+"_score")) else None
                   for key in ("quality", "preference")}
    if a != "valid" or b != "valid" or not number(x) or not number(y):
        return {"outcome": "unresolved", "reason": "score_incomplete", **differences}
    delta = x-y
    return {"outcome": "win" if delta > margin else "loss" if delta < -margin else "tie",
            "reason": "score_comparison", "score_difference": delta,
            **differences}


def make_pairs(rows, baseline_method, margin=3):
    if not number(margin) or margin < 0:
        raise ValueError("tie_margin must be nonnegative and finite")
    lookup = {}
    for row in rows:
        key = (row["method"], match_key(row))
        if key in lookup:
            raise ValueError("Duplicate method / matched condition; assign explicit repetition IDs")
        lookup[key] = row
    result = []
    for new in rows:
        if new["method"] == baseline_method or not new.get("executed", True):
            continue
        base = lookup.get((baseline_method, match_key(new)))
        if base is None or not base.get("executed", True):
            result.append({"new_run": new["run_id"], "method": new["method"],
                           "baseline": baseline_method, "status": "unmatched", "reason": "baseline_condition_missing",
                           "query_id": new["query_id"], "cohort": cohort_key(new), "protocol": new["protocol"]})
            continue
        result.append({"new_run": new["run_id"], "baseline_run": base["run_id"],
                       "method": new["method"], "baseline": baseline_method,
                       "status": "matched", "protocol": new["protocol"], "cohort": cohort_key(new),
                       "query_id": new["query_id"], "query_group": new.get("query_group", new["query_id"]),
                       "both_valid": new["validity"] == base["validity"] == "valid",
                       "strict_budget_comparison": new["protocol"] == "published_v1",
                       **outcome(new, base, margin)})
    return result


def query_mean(rows, field):
    buckets = defaultdict(list)
    for row in rows:
        if number(row.get(field)):
            buckets[row.get("query_group", row["query_id"])].append(row[field])
    return statistics.mean(statistics.mean(v) for v in buckets.values()) if buckets else None


def clustered_interval(rows, field):
    groups = defaultdict(list)
    for row in rows:
        if number(row.get(field)):
            groups[row.get("query_group", row["query_id"])].append(row[field])
    means = [statistics.mean(values) for values in groups.values()]
    if len(means) < 2:
        return {"interval": None, "reason": "fewer_than_two_independent_queries"}
    rng = random.Random(20261111)
    samples = sorted(statistics.mean(rng.choices(means, k=len(means))) for _ in range(1000))
    return {"interval": [samples[24], samples[974]], "method": "query_cluster_bootstrap_1000",
            "query_count": len(means), "small_sample": len(means) < 20}


def aggregate(rows, pairs):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["method"], cohort_key(row))].append(row)
    summaries = []
    for (method, cohort), group in groups.items():
        executed = [r for r in group if r.get("executed", True)]
        scored = [r for r in executed if r["validity"] == "valid"]
        strict = group[0]["protocol"] == "published_v1"
        feasible = [{**r, "success": float(r.get("feasible_by_budget") is True)} for r in executed]
        flags = [{**r, **{key+"_value": float(r[key]) if isinstance(r.get(key), bool) else None
                         for key in ("first_result_late", "final_delivery_late")},
                  "hard_timeout_value": float(r.get("stop_reason") == "hard_timeout"),
                  "budget_exhausted_value": float(r.get("stop_reason") in {"wall_limit_reached", "dfs_budget_reached", "budget_exhausted"})}
                 for r in executed]
        seconds = [r["planning_seconds"] for r in executed if number(r.get("planning_seconds"))]
        paired = [p for p in pairs if p.get("status") == "matched" and p["method"] == method and p["cohort"] == cohort]
        counts = Counter(p["outcome"] for p in paired)
        observed = [{**p, "win": float(p["outcome"] == "win")} for p in paired]
        common = [p for p in paired if p.get("both_valid")]
        for p in common:
            p.setdefault("quality_difference", None)
        summaries.append({
            "method": method, "cohort": cohort, "protocol": group[0]["protocol"],
            "scope": group[0]["scope"], "profile": group[0]["profile"], "budget": group[0]["budget"],
            "planned_runs": len(group), "executed_runs": len(executed),
            "query_count": len({r.get("query_group", r["query_id"]) for r in executed}),
            "validity_counts": dict(Counter(r["validity"] for r in executed)),
            "feasible_by_budget": query_mean(feasible, "success") if strict else None,
            "budget_measurement_unknown_count": sum(r.get("measurement_status") == "unknown" for r in executed),
            "feasible_interval": clustered_interval(feasible, "success") if strict else {"interval": None, "reason": "legacy_excluded"},
            "quality_mean": query_mean(scored, "quality_score"), "preference_mean": query_mean(scored, "preference_score"),
            "quality_known_runs": sum(number(r.get("quality_score")) for r in scored),
            "preference_known_runs": sum(number(r.get("preference_score")) for r in scored),
            "comparison_counts": dict(counts), "matched_count": len(paired),
            "unmatched_count": sum(p.get("status") == "unmatched" and p["method"] == method and p["cohort"] == cohort for p in pairs),
            "confirmed_win_rate": query_mean(observed, "win"),
            "strict_budget_win_rate": query_mean(observed, "win") if strict else None,
            "win_interval": clustered_interval(observed, "win"),
            "common_valid_count": len(common), "paired_quality_difference": query_mean(common, "quality_difference"),
            "paired_preference_difference": query_mean(common, "preference_difference"),
            "planning_seconds_median": statistics.median(seconds) if seconds else None,
            "planning_seconds_range": [min(seconds), max(seconds)] if seconds else None,
            "first_valid_observed_count": sum(number(r.get("first_valid_seconds")) for r in executed),
            "first_valid_censored_count": sum(r.get("first_valid_censored") is True for r in executed),
            "stop_reason_counts": dict(Counter(r.get("stop_reason", "unknown") for r in executed)),
            "first_result_late_count": sum(r.get("first_result_late") is True for r in executed),
            "final_delivery_late_count": sum(r.get("final_delivery_late") is True for r in executed),
            "first_result_late_rate": query_mean(flags, "first_result_late_value"),
            "final_delivery_late_rate": query_mean(flags, "final_delivery_late_value"),
            "hard_timeout_rate": query_mean(flags, "hard_timeout_value"),
            "budget_exhausted_rate": query_mean(flags, "budget_exhausted_value"),
            "evaluation_seconds": sum(r.get("evaluation_seconds", 0) for r in executed),
            "judge_request_count": sum(r.get("judge_request_count", 0) for r in executed),
        })
    return summaries
