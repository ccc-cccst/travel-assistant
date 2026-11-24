"""Verify full candidate sequences with recorded responses and singleton shortcuts."""

import argparse
import json
import os
from pathlib import Path
import sys
from time import perf_counter

from activity_replay import activity_options
from live_report import HISTORY, PROJECT, jsonl, read
from live_profile import configure
from experiments.evaluation.common import plan_fingerprint
from experiments.evaluation.validation import validate_plan


class UnexpectedRequest(BaseException):
    pass


def execute(case, mode, output):
    dataset = HISTORY / "queries" / f"{case.removesuffix('_C_r1')}.json"
    query = read(dataset)
    trace = jsonl(HISTORY / "traces" / f"{case}.jsonl")
    source = HISTORY.parents[1]
    configure("http://127.0.0.1:18080/v1")
    sys.path[:0] = [str(HISTORY), str(source)]
    os.chdir(source)
    from chinatravel.agent.llms import create_llm
    from chinatravel.data.load_datasets import ORACLE_FIELDS
    from chinatravel.environment.world_env import WorldEnv
    from experiment_agent import ExperimentAgent

    llm = create_llm("qwen3.5-4b-q8", max_tokens=4096)
    count, skipped, seconds = 0, 0, 0.

    def send(messages, request_args):
        nonlocal count, skipped, seconds
        if count >= len(trace):
            raise UnexpectedRequest("Recorded trace exhausted")
        if trace[count]["messages"] != messages:
            raise UnexpectedRequest(f"Request {count+1} differs from the saved search")
        recorded = trace[count]
        count += 1
        options = activity_options(messages) if recorded["stage"] == "activity_type" else None
        if mode == "singleton" and options is not None and len(options) == 1:
            skipped += 1
            seconds += recorded["elapsed_seconds"]
            return {"role": "assistant", "content": f"Type: {options[0]}"}
        return recorded["assistant"]

    llm._send_request = send
    agent = ExperimentAgent(group="C", env=WorldEnv(lang="zh"), backbone_llm=llm,
                            cache_dir=str(output / "cache"), debug=False, lang="zh",
                            candidate_limit=20, wall_limit_seconds=1750)
    stdout, stderr = sys.stdout, sys.stderr
    started = perf_counter()
    stopped = None
    try:
        success, plan = agent.run({k: v for k, v in query.items() if k not in ORACLE_FIELDS}, load_cache=False)
    except UnexpectedRequest as error:
        stopped = str(error)
        success, plan = False, {}
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    elapsed = perf_counter() - started
    candidates = [(plan_fingerprint(item["plan"]), item["score"]["total_penalty"])
                  for item in agent.pool.items]
    selected = plan_fingerprint(plan) if success else None
    os.chdir(PROJECT)
    audit = validate_plan(plan, query)["status"] if selected else None
    record = dict(case=case, mode=mode, trace_requests=len(trace), matched_requests=count,
                  skipped=skipped, historical_request_seconds=seconds, replay_seconds=elapsed,
                  stop=stopped, candidates=candidates, selected=selected, audit=audit)
    (output / f"{mode}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(case, mode, f"matched={count}/{len(trace)}", f"skipped={skipped}", f"candidates={len(candidates)}",
          f"audit={audit}", f"elapsed={elapsed:.2f}s", stopped or "", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case")
    parser.add_argument("mode", choices=("original", "singleton"))
    parser.add_argument("--out", type=lambda p: Path(p).resolve(), required=True)
    args = parser.parse_args()
    if not args.case.isidentifier():
        parser.error("Invalid case name")
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / f"{args.mode}.json").exists():
        raise FileExistsError(args.out / f"{args.mode}.json")
    execute(args.case, args.mode, args.out)


if __name__ == "__main__":
    main()
