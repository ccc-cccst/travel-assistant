"""Replay EXP006 requests and verify transport response bypass preserves itineraries."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys
import time

from live_report import HISTORY, jsonl, read, verify
from live_suite import digest


def run(out):
    from experiments.evaluation.common import plan_fingerprint

    source = HISTORY.parents[1]
    manifest = read(HISTORY / "manifest.json")
    for file, expected in manifest["source_sha256"].items():
        if digest(source / file) != expected:
            raise ValueError(f"Changed replay source: {file}")
    sys.path[:0] = [str(source), str(source / "reproduction"), str(HISTORY)]
    os.chdir(source)
    os.environ.pop("CHINATRAVEL_ZH_DATABASE", None)
    os.environ.update(CHINATRAVEL_OPENAI_BASE_URL="http://127.0.0.1:18080/v1",
                      CHINATRAVEL_OPENAI_API_KEY="local-no-key", CHINATRAVEL_OPENAI_WIRE_API="chat",
                      CHINATRAVEL_OPENAI_TOKEN_LIMIT_ARG="max_tokens", CHINATRAVEL_OPENAI_RAISE_ERRORS="1")

    from chinatravel.agent.llms import create_llm
    from chinatravel.agent.nesy_agent.prompts import (
        INTERCITY_TRANSPORT_GO_INSTRUCTION, INTERCITY_TRANSPORT_BACK_INSTRUCTION,
    )
    from chinatravel.data.load_datasets import ORACLE_FIELDS
    from chinatravel.environment.world_env import WorldEnv
    from experiment_agent import ExperimentAgent

    markers = [prompt.split("{transport_info}")[0].split("{user_requirements}")[-1].strip()
               for prompt in (INTERCITY_TRANSPORT_GO_INSTRUCTION, INTERCITY_TRANSPORT_BACK_INSTRUCTION)]
    cases = ("easy_1_C_r1", "easy_2_C_r1", "easy_4_C_r1")
    results = {}
    for case in cases:
        entry = next(item for item in manifest["cases"] if item["case"] == case)
        full_query = read(HISTORY / entry["query"])
        query = {key: value for key, value in full_query.items() if key not in ORACLE_FIELDS}
        trace = jsonl(HISTORY / "traces" / f"{case}.jsonl")
        rows = {}
        for variant in ("baseline", "bypass"):
            folder = out / case / variant
            folder.mkdir(parents=True, exist_ok=False)
            llm = create_llm("qwen3.5-4b-q8", max_tokens=4096)
            index = 0
            bypassed = []

            def replay(messages, request_args):
                nonlocal index
                if index >= len(trace) or messages != trace[index]["messages"]:
                    raise ValueError(f"Replay diverged on {case} {variant}, request {index + 1}")
                record = trace[index]
                index += 1
                content = messages[-1]["content"]
                if variant == "bypass" and any(marker in content for marker in markers):
                    bypassed.append(record["elapsed_seconds"])
                    return {"role": "assistant", "content": "IDList: []"}
                return record["assistant"]

            llm._send_request = replay
            start = time.perf_counter()
            agent = ExperimentAgent(group="C", env=WorldEnv(lang="zh"), backbone_llm=llm,
                                    cache_dir=str(folder / "cache"), log_dir=str(folder / "cache"),
                                    debug=True, lang="zh", candidate_limit=20, wall_limit_seconds=1750)
            with (folder / "worker.log").open("w", encoding="utf-8") as log:
                with redirect_stdout(log), redirect_stderr(log):
                    success, plan = agent.run(dict(query), load_cache=False)
            seconds = time.perf_counter() - start
            if index != len(trace) or not success:
                raise ValueError(f"Incomplete replay of {case} {variant} at {index}/{len(trace)}")
            selected = folder / "plan.json"
            from chinatravel.data.load_datasets import NpEncoder
            selected.write_text(json.dumps(plan, ensure_ascii=False, indent=2, cls=NpEncoder), "utf-8")
            rows[variant] = {"wall_seconds_offline": seconds, "matched_requests": index,
                             "bypassed_calls": len(bypassed), "historical_bypassed_seconds": sum(bypassed),
                             "search_nodes": agent.search_nodes, "backtrack_count": agent.backtrack_count,
                             "candidate_sequence": [(plan_fingerprint(item["plan"]), item["score"]["total_penalty"])
                                                    for item in agent.pool.items],
                             "selected_fingerprint": plan_fingerprint(plan),
                             "audit": verify(selected, full_query, success)}
        for field in ("candidate_sequence", "selected_fingerprint", "search_nodes", "backtrack_count"):
            if rows["baseline"][field] != rows["bypass"][field]:
                raise AssertionError(f"{case} changed {field}")
        if rows["baseline"]["selected_fingerprint"] != plan_fingerprint(read(HISTORY / "results" / f"{case}.json")):
            raise AssertionError(f"{case} historical selected itinerary was not reproduced")
        print(case, len(rows["bypass"]["candidate_sequence"]), rows["bypass"]["bypassed_calls"], flush=True)
        results[case] = rows
    (out / "replay.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    run(parser.parse_args().out.resolve())
