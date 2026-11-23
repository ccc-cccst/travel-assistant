"""Measure step-level and half-day LLM decisions on the historical five easy cases."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

from activity_granularity import experiment_agent


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CASES = tuple(f"easy_{i}" for i in range(1, 6))
SEED = 20261112
LIMIT = 20


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def measure(args):
    from types import SimpleNamespace
    sys.path[:0] = [str(ROOT / "experiments/c_only"), str(ROOT)]
    import experiment_agent as historical
    import live_profile

    paths = []
    observed = []
    started = time.perf_counter()
    original = historical.ExperimentAgent

    def capture(agent):
        paths.append(agent)
        first = agent.on_candidate

        def callback(pool):
            item = pool.items[-1]
            observed.append({"fingerprint": item["fingerprint"], "seconds": time.perf_counter() - started})
            first(pool)

        agent.on_candidate = callback

    if args.arm == "personalized":
        from chinatravel.personalization.agent import personalized_agent
        query=read(args.query)
        historical.ExperimentAgent = personalized_agent(original, capture, query['request_v1'],
            candidate_recall=query.get('experiment_options',{}).get('candidate_recall','baseline'))
    elif args.arm == "combo_schedule":
        from experiments.timing_probe.combo_schedule import combination_agent
        historical.ExperimentAgent = combination_agent(original, capture)
    elif args.arm == "day_sets":
        from day_sets import daily_agent
        historical.ExperimentAgent = daily_agent(original, capture)
    else:
        historical.ExperimentAgent = experiment_agent(original, args.arm, capture)
    flags = SimpleNamespace(source=ROOT, query=args.query, out=args.out, seconds=args.seconds, arm="shorttrip",
        max_tokens=4096, candidate_limit=getattr(args, 'candidate_limit', LIMIT), seed=SEED, endpoint=args.endpoint, original_stops=False,
        preflight=False, return_batch_size=0, adaptive_rescue=False, concise_ranking=False,
        time_policy=args.time_policy)
    live_profile.run(flags)
    save(args.out / "candidate_times.json", observed)
    from route_view import route_markdown, route_view
    plan = read(args.out / "plan.json")
    view = route_view(plan, getattr(paths[0], 'query', {}).get("appointments", ()) if paths else (),policy=args.time_policy)
    save(args.out / "route.json", view)
    (args.out / "ROUTE.md").write_text(route_markdown(view), encoding="utf-8")
    if paths and args.arm in ("day_sets", "combo_schedule", "personalized"):
        save(args.out / "day_sets.json", paths[0].day_events)
        if args.arm in ('combo_schedule', 'personalized'):
            save(args.out / 'combinations.json', paths[0].combination_events)
        if args.arm == 'personalized':
            save(args.out / 'trip_recall.json', paths[0].trip_recall_report)
    elif paths:
        policy = paths[0].policy
        save(args.out / "half_day_policy.json", {"decisions": policy.decisions, "events": policy.events})


def quality(option):
    from chinatravel.soft_constraints.scoring import duration
    score = option["score"]
    metrics = score["metrics"]
    activities = [a for day in option["plan"]["itinerary"] for a in day["activities"]]
    legs = [leg for a in activities for leg in a.get("transports", ())]
    costs = sum(a["cost"] for a in [*activities, *legs])
    walking = [duration(leg) for leg in legs if leg.get("mode") == "walk"]
    return {
        "cost": round(costs, 2), "walk_minutes": sum(walking) if all(m is not None for m in walking) else None,
        "city_transit_minutes": metrics["total_city_transit_minutes"],
        "sightseeing_minutes": sum(d["attraction_minutes"] for d in metrics["days"]),
        "attractions": len({a["position"] for a in activities if a["type"] == "attraction"}),
        "longest_idle_minutes": max((gap for day in metrics["days"] for gap in day["idle_gaps_minutes"]), default=0),
        "penalty": score["total_penalty"], "penalty_categories": score["categories"],
    }


def audit(out, query, policy='strict'):
    from experiments.evaluation.validation import validate_plan
    run = read(out / "run.json")
    pool = read(out / "pool.json")
    times = {row["fingerprint"]: row["seconds"] for row in read(out / "candidate_times.json")}
    checks = {}
    first_valid = None
    selection_path = out / 'selection.json'
    selected_fingerprint = read(selection_path).get('selected_fingerprint') if selection_path.exists() else None
    selected = next((p for p in pool if p['fingerprint'] == selected_fingerprint), None)
    if selected is None and pool:
        from chinatravel.soft_constraints.pool import fingerprint
        actual = fingerprint(read(out / 'plan.json'))
        selected = next((p for p in pool if p['fingerprint'] == actual), None)
    for option in pool:
        check = validate_plan(option["plan"], query,config={'time_policy':policy})
        valid = all(row["status"] in ("pass", "not_applicable") for row in check["checks"])
        checks[option["fingerprint"]] = {"passes": valid, "checks": check["checks"]}
        if valid and first_valid is None:
            first_valid = times[option["fingerprint"]]
    requests = out / "requests.jsonl"
    ledger = [json.loads(line) for line in requests.read_text(encoding="utf-8").splitlines()] if requests.exists() else []
    activities = [row for row in ledger if row["phase"] == "activity_type"]
    check = checks.get(selected["fingerprint"]) if selected else None
    observed = {
        "wall_seconds": run["wall_seconds"], "stop_reason": run["stop_reason"], "error": run["error"],
        "time_policy":policy,
        "first_independent_legal_seconds": first_valid, "first_internal_seconds": run["first_published_seconds"],
        "internal_candidates": len(pool), "external_legal_candidates": sum(row["passes"] for row in checks.values()),
        "selected_legal": bool(check and check["passes"]),
        "selected_failed_checks": [row["name"] for row in check["checks"] if row["status"] not in ("pass", "not_applicable")] if check else [],
        "model_calls": len(ledger), "model_seconds": sum(row["seconds"] for row in ledger),
        "model_calls_by_phase": dict(Counter(row["phase"] for row in ledger)),
        "activity_model_calls": len(activities), "activity_model_seconds": sum(row["seconds"] for row in activities),
        "dfs_calls": run.get("stats", {}).get("dfs", {}).get("count"),
        "search_nodes": run["search_nodes"], "validation_fallback": run.get("translation_fallback"),
        "selected_quality": quality(selected) if selected else None,
        "selected_fingerprint": selected["fingerprint"] if selected else None,
    }
    save(out / "independent_audit.json", checks)
    save(out / "comparison.json", observed)
    return observed


def campaign(args):
    from chinatravel.environment.sandbox.__main__ import configuration
    env = {**os.environ, **configuration(args.data), "CHINATRAVEL_TRANSLATION_MODE": "chinatravel"}
    os.environ.update(env)
    args.out.mkdir(parents=True, exist_ok=False)
    source_paths = [p for p in (ROOT / "chinatravel").rglob("*.py")
                    if "database" not in p.parts and "__pycache__" not in p.parts]
    source_paths += [*ROOT.glob("experiments/c_only/*.py"), *ROOT.glob("experiments/timing_probe/*.py")]
    inputs = {case: ROOT / "experiments/c_only/queries" / f"{case}.json" for case in args.cases}
    source_paths.extend(inputs.values())
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    save(args.out / "MANIFEST.json", {
        "source": str(ROOT), "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                                 encoding="utf-8").strip(),
        "source_and_request_sha256": hashes, "snapshot": read(args.data / "install.json")["source"],
        "cases": args.cases, "experiment": "step-level activity decisions vs " + args.experiment,
        "experimental_arm": args.experiment,
        "model": "qwen3.5-4b-q8", "max_tokens": 4096, "seed": SEED, "temperature": 0,
        "translation_mode": env["CHINATRAVEL_TRANSLATION_MODE"],
        "time_policy":args.time_policy,
        "budget_seconds": args.seconds, "candidate_limit": LIMIT, "workers": 1, "endpoint": args.endpoint,
    })
    rows = {}
    for index, (case, path) in enumerate(inputs.items()):
        rows[case] = {}
        arms = ("baseline", args.experiment) if index % 2 == 0 else (args.experiment, "baseline")
        for arm in arms:
            out = args.out / f"{case}__{arm}"
            print(f"START {case} {arm}", flush=True)
            save(args.out / "STATUS.json", {"case": case, "arm": arm, "started": time.time(), "completed": rows})
            log = args.out / f"{case}__{arm}.log"
            cmd = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "worker",
                   "--arm", arm, "--query", str(path), "--out", str(out), "--seconds", str(args.seconds),
                   "--endpoint", args.endpoint]
            cmd += ["--time-policy",args.time_policy]
            try:
                with log.open("x", encoding="utf-8") as output:
                    result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
                                            timeout=args.seconds + 50)
                observed = audit(out, read(path),args.time_policy) if result.returncode == 0 else {"process_error": result.returncode}
            except subprocess.TimeoutExpired:
                observed = {"process_error": "outer_watchdog"}
            rows[case][arm] = observed
            save(args.out / "RESULTS.json", rows)
            print(f"DONE {case} {arm}: {observed.get('wall_seconds')}s; "
                  f"calls={observed.get('model_calls')}; legal={observed.get('selected_legal')}", flush=True)
    save(args.out / "STATUS.json", {"finished": True, "completed": rows})
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    complete = [pair for pair in rows.values() if all("wall_seconds" in value for value in pair.values())]
    valid_pairs = [pair for pair in complete if all(value["selected_legal"] for value in pair.values())]
    summary = {
        "complete_pairs": len(complete), "paired_legal_outputs": len(valid_pairs),
        "pair_wall_delta_median": statistics.median([p[args.experiment]["wall_seconds"] - p["baseline"]["wall_seconds"]
                                                     for p in complete]) if complete else None,
        "arms": {arm: {key: sum(pair[arm].get(key, 0) for pair in complete) for key in
                       ("wall_seconds", "model_seconds", "activity_model_seconds", "activity_model_calls",
                        "model_calls", "selected_legal")} for arm in ("baseline", args.experiment)},
    }
    save(args.out / "SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("campaign", "worker"))
    parser.add_argument("--arm", choices=("baseline", "half_day", "day_sets", "combo_schedule", "personalized"))
    parser.add_argument("--candidate-limit",type=int,default=LIMIT)
    parser.add_argument("--experiment", choices=("half_day", "day_sets"), default="half_day")
    parser.add_argument("--time-policy",choices=("strict","flexible"),default="strict")
    parser.add_argument("--query", type=Path)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=240)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18086/v1")
    args = parser.parse_args()
    args.out = args.out.resolve()
    os.chdir(ROOT)
    if args.command == "worker":
        measure(args)
    else:
        campaign(args)


if __name__ == "__main__":
    main()

