"""CPU-only timing decomposition. Exact cached preparation; no model fallback."""
from __future__ import annotations

import time
PROCESS_STARTED = time.perf_counter()
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import shutil
import statistics
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "experiments/prefix_probe"))
spec = importlib.util.spec_from_file_location("prefix_runner", PROJECT / "experiments/prefix_probe/run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from probe import PrefixProbeAgent, freeze, save
from experiments.timing_probe.spans import ExclusiveTimer
from chinatravel.environment.world_env import WorldEnv
from chinatravel.data.load_datasets import ORACLE_FIELDS
import chinatravel.agent.nesy_agent.nesy_agent as nesy_module

IMPORT_SECONDS = time.perf_counter() - PROCESS_STARTED


class ReplayLLM:
    """The existing cache format, with missing requests rejected before any I/O to a model."""
    def __init__(self, directory, seed=20261109, deadline=None):
        self.directory, self.seed = Path(directory), seed
        self.name = "qwen3.5-4b-q8"
        self.input_token_count = self.output_token_count = self.input_token_maxx = 0
        self.cache_hits = self.api_calls = 0
        self.actual_seconds = 0.0
        self.requests = []
        self.deadline = deadline

    def __call__(self, messages, one_line=True, json_mode=False, **kwargs):
        hotel = any("HotelNameList" in str(m.get("content", "")) for m in messages)
        request = {
            "model": self.name, "endpoint": "http://127.0.0.1:18080/v1",
            "seed": self.seed, "temperature": 0, "max_tokens": 2048 if hotel else 4096,
            "messages": messages, "one_line": one_line, "json_mode": json_mode,
            "kwargs": kwargs,
        }
        key = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self.directory / f"{key}.json"
        if not path.exists():
            raise RuntimeError(f"Offline replay cache miss: {key}; live inference is disabled")
        record = runner.read(path)
        if record["spec"] != request:
            raise ValueError("Cache settings differ")
        self.cache_hits += 1
        self.requests.append({"key": key, "historical_seconds": record["elapsed_seconds"]})
        return record["answer"]


def replay_preparation(source, output):
    destination = output / "preparation_replay"
    destination.mkdir()
    snapshot = runner.read(source / "frozen.json")
    runner.check_sources(snapshot)
    llm = ReplayLLM(source / "llm_cache", snapshot["seed"])
    query = runner.read(PROJECT / "experiments/c_only/queries" / f"{snapshot['case']}.json")
    query = {k: v for k, v in query.items() if k not in ORACLE_FIELDS}
    phases = {}
    started = time.perf_counter()
    with (destination / "replay.log").open("x", encoding="utf-8") as stream:
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            t = time.perf_counter()
            agent = runner.CaptureAgent(group="C", env=WorldEnv(lang="zh"), backbone_llm=llm,
                cache_dir=str(destination / "translation_cache"), debug=False, lang="zh",
                wall_limit_seconds=180)
            agent.soft_started = time.monotonic()
            phases["environment_and_agent_init"] = time.perf_counter() - t
            t = time.perf_counter()
            translated = agent.translate_nl2sl(query, load_cache=False)
            phases["translation_cached"] = time.perf_counter() - t
            t = time.perf_counter()
            try:
                agent.symbolic_search(translated)
            except runner.SnapshotReady:
                pass
            if not hasattr(agent, "capture"):
                raise RuntimeError("Replay failed to reach first attraction")
            phases["outer_choices_and_prefix_cached"] = time.perf_counter() - t
            state = agent.capture
            t = time.perf_counter()
            agent.ranking_restaurants(state["plan"], state["poi_plan"], state["current_day"],
                state["current_time"], state["current_position"], agent.intercity_with_hotel_cost)
            phases["freeze_restaurant_recommendations_cached"] = time.perf_counter() - t
    state["agent"] = {key: freeze(getattr(agent, key)) for key in runner.STATE_FIELDS}
    state["memory"] = {key: freeze(frame.to_dict(orient="split")) for key, frame in agent.memory.items()}
    changed = [key for key in snapshot["state"] if state.get(key) != snapshot["state"][key]]
    result = {"scope": "CPU preparation replay; historical answers only, not live end-to-end latency",
        "phases_seconds": phases, "wall_seconds": time.perf_counter() - started,
        "llm_requests_replayed": llm.requests, "live_model_requests": llm.api_calls,
        "historical_successful_request_seconds_sum": sum(r["historical_seconds"] for r in llm.requests),
        "state_matches_original": not changed, "changed_state_fields": changed,
        "format_fallbacks": agent.llm_rec_format_error}
    save(destination / "summary.json", result)
    if changed:
        raise ValueError("Replay does not match frozen state: " + str(changed))
    return result


def timed_search(args, variant, candidates, repeat, measured=False):
    tracer = ExclusiveTimer()
    instances = []
    class MeasuredAgent(PrefixProbeAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)
            if not measured:
                return
            for name in ("ranking_attractions", "ranking_restaurants",
                         "reranking_attractions_with_constraints", "reranking_restaurants_with_constraints"):
                tracer.wrap(self, name, "ranking")
            tracer.wrap(self.env.transportation, "goto", "route_computation")
            for name in ("prune_partial", "missing_today", "meals_possible_after"):
                tracer.wrap(self, name, "partial_checks")
            tracer.wrap(self, "constraints_validation", "complete_validation")
            tracer.wrap(self, "_score", "soft_score")
            self.pool.scorer = self._score
            tracer.wrap(self.pool, "add", "candidate_storage")
            tracer.wrap(self.pool, "best", "candidate_storage")
            tracer.wrap(self, "select_next_poi_type", "activity_policy")
            for name in ("emit", "candidate_found", "record_prune"):
                tracer.wrap(self, name, "probe_events")
            tracer.wrap(self, "dfs_poi", "search_remainder", root=True)
    old_agent, old_llm = runner.PrefixProbeAgent, runner.CachedLocalLLM
    runner.PrefixProbeAgent, runner.CachedLocalLLM = MeasuredAgent, ReplayLLM
    old_eval = None
    if measured:
        old_eval = tracer.wrap(nesy_module, "evaluate_constraints_py", "dsl_constraint_evaluation")
    label = f"{'instrumented' if measured else 'baseline'}_r{repeat}_c{candidates}"
    run_args = argparse.Namespace(out=args.out, label=label, activity_policy="rules",
        nodes=200, seconds=180., candidates=candidates)
    started = time.perf_counter()
    try:
        result = runner.run_one(run_args, variant)
        wall = time.perf_counter() - started
    finally:
        runner.PrefixProbeAgent, runner.CachedLocalLLM = old_agent, old_llm
        if old_eval is not None:
            nesy_module.evaluate_constraints_py = old_eval
    agent = instances[0]
    signatures = [(c["fingerprint"], c["score"]["total_penalty"]) for c in agent.pool.items]
    result["candidate_sequence_sha256"] = hashlib.sha256(json.dumps(signatures).encode()).hexdigest()
    result["driver_wall_seconds"] = wall
    result["outside_search_seconds"] = wall - result["elapsed_seconds"]
    result["instrumented"] = measured
    result["repeat"] = repeat
    result["exclusive_timings"] = dict(tracer.stats)
    result["attribution_sum_seconds"] = sum(v["exclusive_seconds"] for v in tracer.stats.values())
    result["unattributed_search_seconds"] = result["elapsed_seconds"] - result["attribution_sum_seconds"] if measured else None
    if result["error"] or result["llm_api_calls"] or result["llm_cache_hits"]:
        raise RuntimeError("Search failed or unexpectedly attempted LLM use")
    save(args.out / label / variant / "timing.json", result)
    return result


def summarize(rows):
    groups = []
    for variant in runner.VARIANTS:
        for cap in (1, 20, 0):
            sample = [r for r in rows if r["variant"] == variant and (r["candidate_limit"] or 0) == cap]
            baseline = [r for r in sample if not r["instrumented"]]
            measured = next(r for r in sample if r["instrumented"])
            fingerprints = {r["candidate_sequence_sha256"] for r in sample}
            if len(fingerprints) != 1:
                raise ValueError(f"Instrumentation/repeats changed candidate sequence: {variant}, {cap}")
            times = [r["elapsed_seconds"] for r in baseline]
            median = statistics.median(times)
            groups.append({"variant": variant, "candidate_limit": cap or None,
                "baseline_seconds": times, "median_seconds": median,
                "min_seconds": min(times), "max_seconds": max(times),
                "median_first_candidate_seconds": statistics.median(r["first_candidate"]["elapsed_seconds"] for r in baseline),
                "median_after_first_candidate_seconds": statistics.median(r["elapsed_seconds"] - r["first_candidate"]["elapsed_seconds"] for r in baseline),
                "candidate_count": measured["candidate_count"], "dfs_entries": measured["dfs_entries"],
                "search_nodes": measured["counters"]["search_nodes"], "best_penalty": measured["best_penalty"],
                "candidate_sequence_identical": True, "selected_C_audit": measured["selected_C_audit"],
                "instrumented_seconds": measured["elapsed_seconds"],
                "instrumented_vs_baseline_ratio": measured["elapsed_seconds"] / median,
                "exclusive_timings": measured["exclusive_timings"],
                "unattributed_search_seconds": measured["unattributed_search_seconds"]})
    return groups


def main():
    import os
    os.chdir(PROJECT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT / "artifacts/prefix_probe/EXP001_easy_1")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    args.source, args.out = args.source.resolve(), args.out.resolve()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / "frozen.json").exists():
        raise FileExistsError("Choose a new output directory; measurements cannot be overwritten")
    shutil.copyfile(args.source / "frozen.json", args.out / "frozen.json")
    save(args.out / "manifest.json", {"import_seconds_single_process": IMPORT_SECONDS,
        "source": str(args.source), "source_frozen_sha256": runner.digest(args.source / "frozen.json"),
        "measurement_source_sha256": runner.digest(Path(__file__)), "repeats": args.repeats,
        "scope": "Preparation exact-cache replay plus frozen rules search; no live model or GPU",
        "timing_definitions": {"exclusive": "Additive named spans; subtract all instrumented child spans",
            "inclusive": "Overlapping spans; do not sum across categories",
            "search_remainder": "DFS work outside named spans, including copying, some preflight checks and log formatting; not recursion overhead alone"}})
    preparation = replay_preparation(args.source, args.out)
    print(f"Preparation replay matches snapshot: {preparation['state_matches_original']}", flush=True)
    rows = []
    for repeat in range(1, args.repeats + 1):
        configs = [(variant, cap) for variant in runner.VARIANTS for cap in (1, 20, 0)]
        random.Random(20261111 + repeat).shuffle(configs)
        for variant, cap in configs:
            rows.append(timed_search(args, variant, cap, repeat))
    for variant in runner.VARIANTS:
        for cap in (1, 20, 0):
            rows.append(timed_search(args, variant, cap, 1, measured=True))
    groups = summarize(rows)
    save(args.out / "COMPARISON.json", {"preparation": preparation, "groups": groups,
        "all_live_model_requests": sum(r["llm_api_calls"] for r in rows),
        "all_search_model_cache_hits": sum(r["llm_cache_hits"] for r in rows),
        "run_count": len(rows), "all_candidate_sequences_preserved": True})
    print(f"Completed {len(rows)} searches; candidate sequences identical across repetitions and instrumentation.")


if __name__ == "__main__":
    main()
