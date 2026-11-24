"""CLI: offline evaluation, no planning and no automatic model startup."""
import argparse
import copy
from datetime import datetime, timezone
import time
from pathlib import Path

from . import VERSION
from .adapters import import_legacy, import_manifest, publication_state
from .common import (HERE, PROJECT, digest, file_digest, merged, number,
                     plan_fingerprint, read_json, write_json, write_jsonl)
from .comparison import aggregate, make_pairs
from .report import report_text


def evaluator_sources():
    files = list(HERE.glob("*.py")) + [HERE / "config.v0.json", HERE / "requirements.v0.json"]
    files += list((PROJECT / "chinatravel/symbol_verification").glob("*.py"))
    files += list((PROJECT / "chinatravel/environment").rglob("*.py"))
    files += [PROJECT / "experiments/c_only/rules.py", PROJECT / "chinatravel/evaluation/output_schema.json"]
    return {str(p.relative_to(PROJECT)): file_digest(p) for p in sorted(files)}


def validation_data_sources():
    files = []
    for name in ("database", "database_en"):
        directory = PROJECT / "chinatravel/environment" / name
        if directory.exists():
            files.extend(p for p in directory.rglob("*") if p.is_file())
    return {str(p.relative_to(PROJECT)): file_digest(p) for p in sorted(files)}


class Evaluator:
    def __init__(self, config, *, cache_dir, allow_requests=False):
        self.config, self.cache_dir, self.allow_requests = config, cache_dir, allow_requests
        self.cache, self.cache_hits, self.judge_request_count = {}, 0, 0

    def score(self, plan, run):
        from .validation import validate_plan
        from .features import score_plan
        from .judge import judge_plan
        key = digest({"plan": plan, "query": run["query"], "resources": run["resource_sha256"],
                      "requirements": run.get("requirements"), "config": self.config})
        if key in self.cache:
            self.cache_hits += 1
            result = copy.deepcopy(self.cache[key])
            result["cache_hit"] = True
            result["evaluation_seconds"] = 0
            result["judge_request_count"] = 0
            return result
        started = time.perf_counter()
        validation = validate_plan(plan, run["query"], config=self.config.get("validation"),
                                   requirements=run.get("requirements"))
        semantic = None
        scores = {}
        if validation["status"] == "valid":
            if self.config["profile"] == "full_v0":
                judge_config = dict(self.config.get("judge", {}))
                judge_config["experience_applicable"] = self.config.get("quality", {}).get("applicability", {}).get("E", True)
                semantic = judge_plan(plan, run["query"], requirements=run.get("requirements"),
                                      resources=run.get("resources"), config=judge_config,
                                      cache_dir=self.cache_dir, allow_requests=self.allow_requests)
            scores = score_plan(plan, run["query"], profile=self.config["profile"],
                                requirements=run.get("requirements"), config=self.config.get("quality"),
                                semantic=semantic, resources=run.get("resources"))
        count = (semantic or {}).get("request_count", 0)
        self.judge_request_count += count
        result = {"fingerprint": plan_fingerprint(plan), "validation": validation,
                  "scores": scores, "semantic": semantic, "cache_hit": False,
                  "evaluation_seconds": time.perf_counter()-started, "judge_request_count": count}
        self.cache[key] = copy.deepcopy(result)
        return result


def evaluate(input_path, output_dir, *, config=None, baseline="rank_fixed", all_candidates=False,
             cache_dir=None, allow_requests=False, requirements_override=None):
    started = time.perf_counter()
    config = merged(read_json(HERE / "config.v0.json"), config or {})
    from .judge import resolve_config
    config["judge"] = resolve_config(config.get("judge"))
    if config["profile"] not in {"fast_rules_v0", "full_v0"}:
        raise ValueError("Unknown scoring profile")
    path, out = Path(input_path).resolve(), Path(output_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite {out}")
    runs = import_manifest(path) if path.is_file() else import_legacy(path)
    if requirements_override is not None:
        if len({r["query_sha256"] for r in runs}) != 1:
            raise ValueError("--requirements override is only permitted for one unique query")
        for run in runs:
            run["requirements"] = requirements_override
    sources = evaluator_sources()
    data_sources = validation_data_sources()
    identity = digest({"config": config, "sources": sources, "validation_data": data_sources})
    out.mkdir(parents=True, exist_ok=False)
    evaluator = Evaluator(config, cache_dir=cache_dir or out / "judge_cache", allow_requests=allow_requests)
    rows, plan_rows, curves, blind_records = [], [], [], []
    for run in runs:
        begun = time.perf_counter()
        requests_before = evaluator.judge_request_count
        state = publication_state(run)
        metadata = {k: run.get(k) for k in ("run_id", "method", "case_id", "query_id", "query_group", "query_sha256",
                    "resource_sha256", "scope", "protocol", "repetition", "seed", "order_id", "budget",
                    "activity_policy", "executed", "stop_reason", "planning_seconds", "legacy_penalty")}
        metadata["query_group"] = run.get("query_group") or run["query_id"]
        metadata.update(profile=config["profile"], evaluation_identity=identity,
                        requirements_sha256=digest(run.get("requirements")),
                        measurement_status=state["measurement_status"],
                        measurement_issues=run.get("measurement_issues", [])+state.get("measurement_issues", []))
        if state.get("planning_seconds") is not None:
            metadata["planning_seconds"] = state["planning_seconds"]
        if state.get("stop_reason") is not None:
            metadata["stop_reason"] = state["stop_reason"]
        selected = state.get("selected_plan") if run.get("executed", True) else {}
        row = {**metadata, "validity": "no_result" if run.get("executed", True) else "pending",
               "quality_score": None, "preference_score": None, "composite_score": None,
               "first_valid_seconds": None, "first_valid_censored": None,
               "first_result_late": state.get("first_result_late"), "final_delivery_late": state.get("final_delivery_late"),
               "selected_at_seconds": state.get("selected_at_seconds"),
               "feasible_by_budget": None}
        processed = {}
        def process(plan, role):
            fp = plan_fingerprint(plan)
            if fp in processed:
                return processed[fp]
            result = evaluator.score(plan, run)
            processed[fp] = result
            plan_rows.append({"run_id": run["run_id"], "role": role, "query_id": run["query_id"], **result})
            if result["validation"]["status"] == "valid":
                blind_records.append({"query_id": run["query_id"], "query_group": metadata["query_group"], "query": run["query"], "plan": plan,
                                      "fingerprint": fp, "method": run["method"], "profile": config["profile"],
                                      **{k: result["scores"].get(k) for k in ("quality_score", "preference_score", "composite_score")}})
            return result
        if selected:
            result = process(selected, "selected")
            row["validity"] = result["validation"]["status"]
            row["selected_fingerprint"] = result["fingerprint"]
            row.update({k: result["scores"].get(k) for k in
                        ("quality_score", "preference_score", "composite_score", "quality_status", "preference_status", "coverage")})
        if run["protocol"] == "published_v1" and run.get("executed", True):
            deadline = run["budget"]["limit"]
            valid_times = []
            for pub in state["publications"]:
                if pub["at_seconds"] <= deadline:
                    evaluated = process(pub["plan"], "published")
                    if evaluated["validation"]["status"] == "valid":
                        valid_times.append(pub["at_seconds"])
            if state["measurement_status"] == "known":
                row["feasible_by_budget"] = row["validity"] == "valid" if row["validity"] != "unknown" else None
                row["first_valid_seconds"] = min(valid_times) if valid_times else None
                row["first_valid_censored"] = not bool(valid_times)
            for point in sorted(set(t for t in (1, 3, 5, 10, 15, deadline) if t <= deadline)):
                pubs = [p for p in state["publications"] if p["at_seconds"] <= point]
                result = process(pubs[-1]["plan"], "published") if pubs else None
                curves.append({"run_id": run["run_id"], "at_seconds": point,
                               "measurement_status": state["measurement_status"],
                               "fingerprint": pubs[-1]["fingerprint"] if pubs else None,
                               "validity": result["validation"]["status"] if result else "no_result",
                               "quality_score": (result or {}).get("scores", {}).get("quality_score"),
                               "preference_score": (result or {}).get("scores", {}).get("preference_score"),
                               "composite_score": (result or {}).get("scores", {}).get("composite_score")})
        if all_candidates:
            values = []
            for candidate in run.get("candidates", []):
                result = process(candidate["plan"], "pool_diagnostic")
                if result["validation"]["status"] == "valid" and number(result["scores"].get("composite_score")):
                    values.append((result["scores"]["composite_score"], result["fingerprint"]))
            row["pool_oracle"] = {"diagnostic_only": True, "best_score": max(values)[0] if values else None,
                                  "fingerprint": max(values)[1] if values else None}
        row["evaluation_seconds"] = time.perf_counter()-begun
        row["judge_request_count"] = evaluator.judge_request_count-requests_before
        rows.append(row)
    pairs = make_pairs(rows, baseline, config["tie_margin"])
    summaries = aggregate(rows, pairs)
    manifest = {"schema_version": 1, "evaluation_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "profile": config["profile"], "status": "uncalibrated_proxy", "baseline": baseline,
                "config": config, "evaluation_identity": identity, "source_sha256": sources,
                "validation_data_sha256": data_sources,
                "input": str(path), "input_sources": {r["run_id"]: r.get("source_hashes", {}) for r in runs},
                "evaluation_seconds": time.perf_counter()-started, "judge_request_count": evaluator.judge_request_count,
                "plan_cache_hits": evaluator.cache_hits,
                "notes": ["No human labels inferred", "legacy excluded from strict budget comparison"]}
    for name, data in (("plan_scores", plan_rows), ("run_metrics", rows), ("pairs", pairs), ("budget_curve", curves),
                       ("blind_records", blind_records)):
        write_jsonl(out / (name+".jsonl"), data)
    write_json(out / "manifest.json", manifest)
    write_json(out / "summary.json", summaries)
    (out / "REPORT.md").write_text(report_text(manifest, summaries, pairs, rows), encoding="utf-8")
    return {"output": str(out), "run_count": len(rows), "plan_count": len(plan_rows),
            "judge_request_count": evaluator.judge_request_count, "summaries": summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="legacy run directory or published_v1 manifest JSON")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--requirements", type=Path, help="reviewed requirements for a single-query input")
    parser.add_argument("--profile", choices=("fast_rules_v0", "full_v0"))
    parser.add_argument("--baseline", default="rank_fixed")
    parser.add_argument("--all-candidates", action="store_true", help="additional offline pool diagnosis")
    parser.add_argument("--judge-cache", type=Path)
    parser.add_argument("--allow-judge-requests", action="store_true", help="also requires judge mode=enabled and a pinned model version")
    args = parser.parse_args()
    config = read_json(args.config) if args.config else {}
    if args.profile:
        config["profile"] = args.profile
    result = evaluate(args.input, args.out, config=config, baseline=args.baseline,
                      all_candidates=args.all_candidates, cache_dir=args.judge_cache,
                      allow_requests=args.allow_judge_requests,
                      requirements_override=read_json(args.requirements) if args.requirements else None)
    print(f"Report: {result['output']}\nRuns: {result['run_count']}; real judge requests: {result['judge_request_count']}")


if __name__ == "__main__":
    main()
