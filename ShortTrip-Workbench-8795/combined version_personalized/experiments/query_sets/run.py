"""Run the ten-city plus Xi'an regression prompts against ShortTrip's C planner."""

import argparse
import ast
from collections import Counter
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from chinatravel.agent.nesy_agent.constraint_compiler import compile_constraint
from chinatravel.agent.utils import NpEncoder
from chinatravel.environment.world_env import WorldEnv

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = Path(__file__).with_name("ten_city_xian_v1.json")
PROFILE = ROOT / "experiments/timing_probe/live_profile.py"
C_SOURCE = ROOT / "experiments/c_only"
TIERS = ("easy", "medium", "human")
CITY = "西安"
MODEL = "qwen3.5-4b-q8"
SEED = 20261116
MEALS = {"breakfast", "lunch", "dinner"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, cls=NpEncoder, allow_nan=False) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def priced(rows):
    return rows[rows.price.map(lambda price: math.isfinite(price) and price >= 0)]


def prepare(out):
    env = WorldEnv(lang="zh")
    document = read(PROMPTS)
    if os.environ.get("CHINATRAVEL_PROFILE") != document["profile"]:
        raise ValueError("Run through the expanded11 sandbox entry point")
    queries, evidence = [], {}
    hotels = env.accommodations.data[CITY]
    if len(hotels) != len(priced(hotels)):
        raise ValueError("Unpriced Xi'an hotel exposed to the planner")
    estimated = hotels[hotels.price_status.str.startswith("estimated", na=False)]
    for tag in TIERS:
        cases = document["cases"][tag]
        if len(cases) != 10 or sum(case["target_city"] == CITY for case in cases) != 3:
            raise ValueError(f"Wrong tier size or Xi'an allocation: {tag}")
        for case in cases:
            uid, start, target = (case[key] for key in ("uid", "start_city", "target_city"))
            if any(city not in env.support_cities for city in (start, target)) or start == target:
                raise ValueError(f"Invalid cities: {uid}")
            constraints = [
                f"days == {case['days']}", f"people_number == {case['people_number']}",
                f"tickets == {case['people_number']}", *case["hard_logic"],
            ]
            programs = [compile_constraint(value) for value in constraints]
            if any(program is None for program in programs):
                raise ValueError(f"Unsupported evaluation constraint: {uid}")
            query = {key: value for key, value in case.items() if key != "soft_preferences"}
            query.update(tag=tag, hard_logic=constraints, hard_logic_py=programs,
                         limit_rooms=any(value.startswith("rooms ") for value in constraints),
                         limits_room_type=False)
            queries.append(query)
            mode = "train"
            outbound = env.intercitytransport.select(start, target, mode)
            inbound = env.intercitytransport.select(target, start, mode)
            for constraint in constraints:
                if constraint.startswith("return_start_time <= "):
                    time_limit = ast.literal_eval(constraint.split(" <= ", 1)[1])
                    inbound = inbound[inbound.BeginTime <= time_limit]
            if outbound.empty or inbound.empty:
                raise ValueError(f"No priced round-trip {mode} witness: {uid}")
            fields = ["TrainID", "From", "To", "BeginTime", "EndTime", "Cost"]
            evidence[uid] = {"train_options": [len(outbound), len(inbound)],
                             "train_examples": [rows.iloc[0][fields].to_dict() for rows in (outbound, inbound)],
                             "named_entities": {}}
            for constraint in constraints:
                for field, kind in (("attraction_names", "attractions"), ("restaurant_names", "restaurants")):
                    if not constraint.endswith(f" <= {field}"):
                        continue
                    names = ast.literal_eval(constraint.split(" <= ", 1)[0])
                    table = getattr(env, kind).data[target]
                    for name in names:
                        rows = priced(table[table.name == name])
                        coordinates = env.poi.search(target, name)
                        if rows.empty or not isinstance(coordinates, tuple):
                            raise ValueError(f"Unpriced or missing POI: {uid}/{name}")
                        evidence[uid]["named_entities"][name] = {
                            "kind": kind, "price": rows.iloc[0].price, "coordinates": coordinates,
                        }
            for field, kind in (("hotel_price", "accommodations"), ("food_price", "restaurants")):
                limits = [ast.literal_eval(value.split(" <= ", 1)[1])
                          for value in constraints if value.startswith(f"{field} <= ")]
                if limits:
                    rows = priced(getattr(env, kind).data[target])
                    matching = rows[rows.price <= min(limits)]
                    if matching.empty:
                        raise ValueError(f"No {kind} candidate under the cap: {uid}")
                    evidence[uid][f"{kind}_under_cap"] = len(matching)
    if len({q["uid"] for q in queries}) != len(queries):
        raise ValueError("Duplicate UID")
    if {q["start_city"] for q in queries} | {q["target_city"] for q in queries} != set(env.support_cities):
        raise ValueError("The prompts must collectively cover all cities in the profile")
    for query in queries:
        save(out / "queries" / query["tag"] / f"{query['uid']}.json", query)
    examples = ("西安北站", "西安钟楼", "樊记腊汁肉夹馍", "维也纳国际酒店西安北站店")
    route = env.transportation.goto(CITY, "西安北站", "西安钟楼", "10:05", "taxi")
    save(out / "DATA_CHECK.json", {
        "profile": document["profile"], "cities": env.support_cities, "cases": evidence,
        "xian": {"hotel_rows": len(hotels), "unknown_hotel_prices": 0,
                 "estimated_hotel_rows": len(estimated),
                 "estimated_hotel_example": env.accommodations.price_metadata(CITY, estimated.iloc[0])
                 if len(estimated) else None,
                 "coordinates": {name: env.poi.search(CITY, name) for name in examples},
                 "xian_north_to_bell_tower_taxi": route},
    })
    return queries, env


def evaluate(directory, query, plan, env):
    from chinatravel.evaluation.commonsense_constraint import evaluate_commonsense_constraints
    from chinatravel.evaluation.hard_constraint import evaluate_hard_constraints_v2
    from chinatravel.evaluation.schema_constraint import evaluate_schema_constraints
    from chinatravel.symbol_verification.concept_func import allactivities, innercity_transport_cost

    sys.path.insert(0, str(C_SOURCE))
    from rules import audit

    uid = query["uid"]
    index, inputs, plans = [uid], {uid: query}, {uid: plan}
    schema = read(ROOT / "chinatravel/evaluation/output_schema.json")
    _, schema_table, schema_pass = evaluate_schema_constraints(index, plans, schema)
    _, _, commonsense_table, commonsense_pass = evaluate_commonsense_constraints(index, inputs, plans, lang="zh")
    _, _, _, _, hard_table, hard_pass = evaluate_hard_constraints_v2(index, inputs, plans, commonsense_pass, lang="zh")
    for name, table in (("schema", schema_table), ("commonsense", commonsense_table), ("hard", hard_table)):
        table.to_csv(directory / f"{name}.csv", index=False, encoding="utf-8-sig")
    rule_audit = audit(plan, query["days"])
    save(directory / "rules.json", rule_audit)

    total, poi, prices, intercity = 0.0, {}, [], []
    target = query["target_city"]
    for activity in allactivities(plan):
        kind = activity["type"]
        total += activity["cost"] + innercity_transport_cost(activity.get("transports", []))
        if kind in {"train", "airplane"}:
            intercity.append(activity.get("TrainID") or activity.get("FlightID"))
        elif kind in MEALS | {"attraction", "accommodation"}:
            name = activity["position"]
            poi[name] = env.poi.search(target, name)
            if kind == "accommodation":
                prices.append({key: activity[key] for key in activity
                               if key in {"position", "price", "cost", "price_status"}
                               or key.startswith("estimated_")})
    result = {
        "schema_pass": uid in schema_pass, "commonsense_pass": uid in commonsense_pass,
        "hard_pass": uid in hard_pass, "rules_pass": rule_audit["all_pass"],
        "failed_hard_constraints": [value for i, value in enumerate(query["hard_logic"])
                                    if not hard_table.loc[0, f"logic_py_{i}"]],
        "cost": round(total, 2), "poi": poi, "intercity_ids": intercity, "hotel_prices": prices,
    }
    result["all_pass"] = all(result[key] for key in ("schema_pass", "commonsense_pass", "hard_pass", "rules_pass"))
    save(directory / "evaluation.json", result)
    return result


def report(out, queries, results):
    lines = ["# ShortTrip 当前版本三层提示词验收", "",
             "人工编写的新题。西安用例 9 条，原有 10 城用例 21 条。结果实时写入。", "",
             "| 级别 | 完成 | 出行程 | 全部通过 | 西安出行程 | 西安全部通过 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for tier in TIERS:
        rows = [r for r in results if r["tier"] == tier]
        city = [r for r in rows if r["target_city"] == CITY]
        counts = [len(rows), sum(r["generated"] for r in rows), sum(r["all_pass"] for r in rows),
                  sum(r["generated"] for r in city), sum(r["all_pass"] for r in city)]
        lines.append(f"| {tier} | {counts[0]}/10 | {counts[1]} | {counts[2]} | {counts[3]}/3 | {counts[4]}/3 |")
    lines.extend(["", "| 用例 | 线路 | 结果 | 秒 | 费用（元） |",
                  "| --- | --- | --- | ---: | ---: |"])
    for row in results:
        status = row['status'] + ('（翻译降级，需求待确认）' if row.get('translation_fallback') else '')
        lines.append(f"| {row['uid']} | {row['start_city']}→{row['target_city']} | {status} | "
                     f"{row['seconds']:.1f} | {row.get('cost', '—')} |")
    if len(results) < len(queries):
        lines.extend(["", f"剩余 {len(queries) - len(results)} 条。"])
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "prompt_sha256": hashlib.sha256(PROMPTS.read_bytes()).hexdigest(),
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for top in (ROOT / "chinatravel", C_SOURCE, PROFILE.parent, Path(__file__).parent)
                          for path in sorted(top.rglob("*.py"))},
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "seconds": args.seconds, "model": MODEL, "seed": SEED, "candidate_limit": 1, "max_tokens": 4096,
        "data_profile": os.environ.get("CT_SANDBOX_PROFILE_ROOT"),
        "catalog": os.environ.get("CT_CATALOG_PATH"), "data_mode": os.environ.get("CT_DYNAMIC_MODE"),
        "only": args.only,
    }
    saved_manifest = out / "MANIFEST.json"
    if saved_manifest.exists() and read(saved_manifest) != manifest:
        raise ValueError("Run settings changed; use a new output directory")
    queries, env = prepare(out)
    queries = sorted(queries, key=lambda q: (int(q["uid"][-2:]), TIERS.index(q["tag"])))
    if args.only:
        by_uid = {query["uid"]: query for query in queries}
        queries = [by_uid[uid] for uid in args.only]
    save(saved_manifest, manifest)
    results_path = out / "RESULTS.json"
    results = read(results_path) if results_path.exists() else []
    report(out, queries, results)
    if args.prepare_only:
        print("Data check complete.", flush=True)
        return
    opener = build_opener(ProxyHandler({}))
    with opener.open(args.endpoint + "/models", timeout=10) as response:
        if MODEL not in {model["id"] for model in json.load(response)["data"]}:
            raise ValueError(f"Unexpected model at {args.endpoint}")
    finished = {r["uid"] for r in results}
    for query in queries:
        uid, tier = query["uid"], query["tag"]
        if uid in finished:
            continue
        try:
            with opener.open(args.endpoint.removesuffix("/v1") + "/health", timeout=5) as response:
                if json.load(response).get("status") != "ok":
                    raise RuntimeError("The local model is not healthy")
        except (OSError, RuntimeError) as error:
            save(out / "STATUS.json", {"state": "interrupted", "current": uid, "finished": len(results),
                                       "reason": str(error)})
            raise
        directory = out / "runs" / uid
        directory.mkdir(parents=True, exist_ok=True)
        attempt = directory / "profile"
        if attempt.exists():
            raise ValueError(f"Previous incomplete profile for {uid}; preserve it and use a new run directory")
        save(out / "STATUS.json", {"state": "running", "current": uid, "finished": len(results)})
        print(f"{uid} {query['start_city']} -> {query['target_city']}: started", flush=True)
        start = time.monotonic()
        command = [sys.executable, "-B", "-X", "utf8", str(PROFILE), "--arm", "shorttrip", "--source", str(ROOT),
                   "--query", str(out / "queries" / tier / f"{uid}.json"), "--out", str(attempt),
                   "--seconds", str(args.seconds), "--seed", str(SEED), "--endpoint", args.endpoint,
                   "--candidate-limit", "1", "--original-stops"]
        with (directory / "worker.log").open("wb") as log:
            try:
                worker = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=args.seconds + 30)
                failure = f"worker_exit_{worker.returncode}" if worker.returncode else "missing_worker_result"
            except subprocess.TimeoutExpired:
                failure = "worker_timeout"
        metrics = read(attempt / "run.json") if (attempt / "run.json").exists() else {}
        if not metrics or metrics["wall_seconds"] > args.seconds + 30:
            reason = failure if not metrics else "worker_deadline_overrun"
            save(out / "STATUS.json", {"state": "interrupted", "current": uid, "finished": len(results),
                                       "reason": reason, "observed_seconds": time.monotonic() - start})
            raise RuntimeError(f"{uid} infrastructure interruption: {reason}. See {directory}")
        row = {"uid": uid, "tier": tier, "start_city": query["start_city"], "target_city": query["target_city"],
               "generated": bool(metrics.get("internal_success", False)), "all_pass": False,
               "seconds": round(metrics.get("wall_seconds", time.monotonic() - start), 2),
               "status": metrics.get("stop_reason", failure), "error": metrics.get("error"),
               "requests": metrics.get("request_count")}
        if metrics.get("translation_fallback"):
            row["translation_fallback"] = metrics["translation_fallback"]
        if row["generated"]:
            try:
                row.update(evaluate(directory, query, read(attempt / "plan.json"), env))
                row["status"] = "passed" if row["all_pass"] else "evaluation_failed"
            except Exception as error:
                row.update(status="evaluation_error", error={"type": type(error).__name__, "message": str(error)})
        results.append(row)
        save(results_path, results)
        report(out, queries, results)
        print(f"{uid}: {row['status']} {row['seconds']:.1f}s", flush=True)
    save(out / "STATUS.json", {"state": "completed", "finished": len(results),
                               "outcomes": dict(Counter(r["status"] for r in results))})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18080/v1")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    endpoint = urlparse(args.endpoint)
    if endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.path != "/v1":
        parser.error("The experiment requires a loopback model endpoint")
    if args.seconds <= 0:
        parser.error("The case budget must be positive")
    kernel = ctypes.windll.kernel32 if os.name == "nt" and not args.prepare_only else None
    if kernel and not kernel.SetThreadExecutionState(0x80000001):
        print("Windows did not grant the process a system-awake request.", file=sys.stderr)
    try:
        run(args)
    finally:
        if kernel:
            kernel.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
