"""EXP003: paired ordinary DFS and resumable first-attraction scheduling."""

import argparse
import contextlib
import itertools
from pathlib import Path
import random
import sys
import time
import traceback

from .search import BudgetAgent, ProbeBudget, PROJECT
from experiments.prefix_probe import run as frozen
from chinatravel.environment.world_env import WorldEnv
from chinatravel.soft_constraints.agent import CandidateCollectionComplete, CandidateDeadline
from experiments.evaluation.adapters import requirement_for, snapshot_resources
from experiments.evaluation.common import file_digest, read_json, write_json
from experiments.evaluation.recorder import RunRecorder


METHODS = ("dfs", "round_robin")
ORDERS = tuple(itertools.permutations(range(3)))
DEFAULT_SOURCE = PROJECT / "artifacts/prefix_probe/EXP001_easy_1/frozen.json"


class OfflineOnly:
    name = "offline_rules"

    def __call__(self, *args, **kwargs):
        raise RuntimeError("Model calls are disabled in this experiment")


def run_one(args, snapshot, shared, method, order, repetition, seconds, nodes):
    order_id = "".join(str(i + 1) for i in order)
    budget_id = f"n{nodes}" if nodes else f"t{seconds:g}"
    directory = args.out / f"{budget_id}_{order_id}_r{repetition}_{method}"
    recorder = RunRecorder(directory, query=str(shared["query"]), method=method,
                           case_id=snapshot["case"], budget_seconds=seconds,
                           resources=str(shared["resources"]), requirements=str(shared["requirements"]),
                           repetition=repetition, order_id=order_id, seed=args.seed)
    if nodes:
        recorder.entry.update(protocol="legacy_posthoc",
                              budget={"kind": "dfs_entries", "limit": nodes, "guard_seconds": seconds})
    recorder.entry["activity_policy"] = "rules"
    publications = []
    discovery = []
    reason, error, agent = "search_exhausted", None, None
    with (directory / "search.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                agent = BudgetAgent(
                    method=method, order=order, ranking=args.ranking, dfs_budget=nodes or sys.maxsize,
                    env=WorldEnv(lang="zh"), backbone_llm=OfflineOnly(),
                    cache_dir=str(directory / "cache"), debug=False, lang="zh",
                    candidate_limit=args.candidate_limit or sys.maxsize, wall_limit_seconds=seconds,
                )
                state = frozen.restore(snapshot, agent)
                agent.started = agent.soft_started = recorder.started
                if agent.attraction_names_visiting or any(
                        a["type"] == "attraction" for day in state["plan"] for a in day["activities"]):
                    raise ValueError("The experiment requires a pre-first-attraction snapshot")

                def candidate_found(pool):
                    item = pool.items[-1]
                    penalty = item["score"]["total_penalty"]
                    first = agent.attraction_names_visiting[0] if agent.attraction_names_visiting else ""
                    agent.branch_candidates[first] += 1
                    found_at = time.monotonic() - recorder.started
                    discovery.append({"at_seconds": found_at, "dfs_entries": agent.dfs_entries,
                                      "fingerprint": item["fingerprint"], "penalty": penalty, "first": first})
                    if found_at <= seconds and (not publications or penalty < publications[-1]["penalty"]):
                        event = recorder.publish(item["plan"])
                        publications.append({"at_seconds": event["at_seconds"], "dfs_entries": agent.dfs_entries,
                                             "fingerprint": item["fingerprint"], "penalty": penalty,
                                             "first": first, "ordinal": item["ordinal"]})
                    agent.wall_check()

                agent.on_candidate = candidate_found
                recorder.event("search_started")
                agent.search(state, args.quantum, args.width)
                agent.wall_check()
            except CandidateCollectionComplete:
                reason = "candidate_limit_reached"
            except CandidateDeadline:
                reason = "wall_limit_reached"
            except ProbeBudget:
                reason = "dfs_budget_reached"
            except Exception as exc:
                reason = "error"
                error = {"type": type(exc).__name__, "message": str(exc)}
                traceback.print_exc()
    recorder.finish(reason)
    pool = agent.pool.items if agent else []
    if pool:
        # The search recorder writes incumbents as they become available.
        # After timing ends, retain the entire pool for retrospective audits.
        write_json(directory / "candidates.json", pool)
    timely = [event for event in publications if event["at_seconds"] <= seconds]
    winner = timely[-1] if timely else None
    selected = pool[winner["ordinal"] - 1] if winner else None
    if nodes and selected:
        write_json(directory / "selected.json", selected["plan"])
    def counts(field):
        return dict(getattr(agent, field, {}))
    entries = counts("branch_entries")
    named = {name: count for name, count in entries.items() if name}
    scheduler = getattr(agent, "scheduler", None)
    summary = {
        "run_id": directory.name, "method": method, "order": order_id, "repetition": repetition,
        "wall_budget": seconds, "node_budget": nodes, "budget_id": budget_id,
        "stop_reason": reason, "error": error, "elapsed_seconds": recorder.entry["planning_seconds"],
        "first_ranked": getattr(agent, "first_order", None),
        "dfs_entries": getattr(agent, "dfs_entries", 0), "search_nodes": getattr(agent, "search_nodes", 0),
        "candidate_count": len(pool), "duplicates": agent.pool.duplicate_count if agent else 0,
        "first_published_seconds": timely[0]["at_seconds"] if timely else None,
        "best_penalty": winner["penalty"] if winner else None, "winner": winner,
        "score_coverage": selected["score"]["coverage"] if selected else None,
        "score_unknown": selected["score"]["unknown"] if selected else None,
        "branch_entries": entries, "branch_seconds": counts("branch_seconds"),
        "branch_routes": counts("branch_routes"), "branch_validations": counts("branch_validations"),
        "branch_candidates": counts("branch_candidates"), "distinct_first_attractions": len(named),
        "max_first_share": max(named.values()) / sum(named.values()) if named else None,
        "admissions": dict(scheduler.admissions) if scheduler else {},
        "slices": dict(scheduler.slices) if scheduler else {},
        "completed": dict(scheduler.completed) if scheduler else {},
        "rounds": scheduler.rounds if scheduler else 0,
        "publications": publications, "discovery_curve": discovery,
        "candidate_signature": frozen.freeze([(c["fingerprint"], c["score"]["total_penalty"]) for c in pool])
            if nodes else None,
        "live_model_calls": 0,
    }
    write_json(directory / "summary.json", summary)
    print(f"{directory.name}: {reason}; {summary['dfs_entries']} entries; "
          f"{len(named)} branches; penalty={summary['best_penalty']}; "
          f"{summary['elapsed_seconds']:.2f}s", flush=True)
    if error:
        raise RuntimeError(f"{directory.name}: {error}; see search.log")
    return summary, recorder.entry


def compare(args):
    args.out = args.out.resolve()
    snapshot = read_json(args.source)
    frozen.check_sources(snapshot)
    query_path = PROJECT / "experiments/c_only/queries" / (snapshot["case"] + ".json")
    if file_digest(query_path) != snapshot["source_query_sha256"]:
        raise ValueError("The evaluation query differs from the frozen source")
    query = read_json(query_path)
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / "config.json", {
        "stage": args.stage, "quantum": args.quantum, "initial_width": args.width,
        "repeats": args.repeats, "wall_seconds": args.seconds, "seed": args.seed,
        "candidate_limit": args.candidate_limit, "ranking": args.ranking, "orders": args.orders,
        "frozen_source": str(args.source.resolve()), "frozen_sha256": file_digest(args.source),
        "source_sha256": {p.name: file_digest(p) for p in Path(__file__).parent.glob("*.py")},
    })
    shared = {name: args.out / f"{name}.json" for name in ("query", "resources", "requirements")}
    write_json(shared["query"], query)
    write_json(shared["resources"], snapshot_resources(snapshot, file_digest(args.source)))
    write_json(shared["requirements"], requirement_for(snapshot["case"], query))
    budgets = [(180.0, 200)] if args.stage == "nodes" else [(seconds, None) for seconds in args.seconds]
    repetitions = 1 if args.stage == "nodes" else args.repeats
    rng = random.Random(args.seed)
    runs, summaries = [], []
    for seconds, nodes in budgets:
        for repetition in range(1, repetitions + 1):
            orders = [tuple(int(i) - 1 for i in order) for order in args.orders]
            rng.shuffle(orders)
            for order in orders:
                methods = list(METHODS)
                rng.shuffle(methods)
                for method in methods:
                    result, entry = run_one(args, snapshot, shared, method, order, repetition, seconds, nodes)
                    summaries.append(result)
                    runs.append(entry)
                    write_json(args.out / "manifest.json", {"schema_version": 1, "runs": runs})
    write_json(args.out / "comparison.json", summaries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stage", choices=("nodes", "wall"), required=True)
    parser.add_argument("--quantum", type=int, default=10)
    parser.add_argument("--width", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seconds", type=float, nargs="+", default=[5, 15])
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--ranking", choices=("original", "rank_fixed"), default="rank_fixed")
    parser.add_argument("--candidate-limit", type=int, default=0, help="0 uses only the search budget")
    order_ids = ["".join(str(i + 1) for i in order) for order in ORDERS]
    parser.add_argument("--orders", nargs="+", choices=order_ids, default=order_ids)
    args = parser.parse_args()
    if min(args.quantum, args.width, args.repeats, *args.seconds) <= 0 or args.candidate_limit < 0:
        parser.error("Search budgets must be positive and the candidate limit non-negative")
    if len(set(args.orders)) != len(args.orders):
        parser.error("Each requested order must be unique")
    compare(args)


if __name__ == "__main__":
    main()
