"""Prepare a frozen real state, compare DFS orderings, and report evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from probe import (
    PROJECT, PrefixProbeAgent, ProbeBudget, activities, digest, diversity, freeze, save,
)
from experiment_agent import ExperimentAgent
from chinatravel.soft_constraints.agent import CandidateCollectionComplete, CandidateDeadline

VARIANTS = ("original", "rank_fixed", "second_first", "third_first")
SOURCES = [
    "experiments/prefix_probe/probe.py", "experiments/prefix_probe/run.py",
    "experiments/c_only/experiment_agent.py", "experiments/c_only/dfs_snapshot.py",
    "experiments/c_only/rules.py", "experiments/c_only/meal_pruning.py",
    "chinatravel/agent/nesy_agent/llm_driven_rec.py",
    "chinatravel/agent/nesy_agent/nesy_agent.py",
    "chinatravel/agent/nesy_agent/prompts/prompts.py",
    "chinatravel/agent/nesy_agent/pruning.py",
    "chinatravel/soft_constraints/agent.py", "chinatravel/soft_constraints/pool.py",
    "chinatravel/soft_constraints/scoring.py", "data_sources.json",
]
STATE_FIELDS = [
    "query", "required_rooms", "required_budget", "intercity_with_hotel_cost",
    "innercity_transports_ranking_from_query", "restaurants_visiting",
    "attractions_visiting", "food_type_visiting", "spot_type_visiting",
    "attraction_names_visiting", "restaurant_names_visiting",
    "suggested_attractions_from_query", "suggested_restaurants_from_query",
]
COUNTERS = [
    "search_nodes", "backtrack_count", "constraints_validation_count",
    "commonsense_pass_count", "logical_pass_count", "all_constraints_pass",
    "llm_rec_count", "llm_rec_format_error", "llm_inference_time_count",
]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class CachedLocalLLM:
    """One answer per exact prompt/settings; cache misses use localhost only."""
    def __init__(self, directory, seed=20261109, deadline=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.name = "qwen3.5-4b-q8"
        self.seed = seed
        self.deadline = deadline
        self.client = None
        self.input_token_count = self.output_token_count = self.input_token_maxx = 0
        self.cache_hits = self.api_calls = 0
        self.actual_seconds = 0.0

    def __call__(self, messages, one_line=True, json_mode=False, **kwargs):
        # Hotel explanations can consume the entire preparation budget on 4B.
        # Only this frozen outer-choice request is capped; all arms share it.
        hotel_request = any("HotelNameList" in str(m.get("content", "")) for m in messages)
        token_limit = 2048 if hotel_request else 4096
        spec = {
            "model": self.name, "endpoint": "http://127.0.0.1:18080/v1",
            "seed": self.seed, "temperature": 0, "max_tokens": token_limit,
            "messages": messages, "one_line": one_line, "json_mode": json_mode,
            "kwargs": kwargs,
        }
        key = hashlib.sha256(json.dumps(
            spec, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        path = self.directory / f"{key}.json"
        if path.exists():
            record = read(path)
            if record["spec"] != spec:
                raise ValueError("LLM cache settings mismatch")
            self.cache_hits += 1
            return record["answer"]
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("Preparation/LLM deadline reached")
        if self.client is None:
            from chinatravel.agent.llms import create_llm
            os.environ.update(
                CHINATRAVEL_OPENAI_BASE_URL=spec["endpoint"],
                CHINATRAVEL_OPENAI_API_KEY="local-no-key",
                CHINATRAVEL_OPENAI_MODEL=self.name,
                CHINATRAVEL_OPENAI_WIRE_API="chat",
                CHINATRAVEL_OPENAI_TOKEN_LIMIT_ARG="max_tokens",
                CHINATRAVEL_OPENAI_RAISE_ERRORS="1",
                NO_PROXY="127.0.0.1,localhost",
            )
            self.client = create_llm(self.name, max_tokens=4096)
            assert self.client._client_kwargs["base_url"] == spec["endpoint"]
            self.client.default_request_args.update(
                temperature=0, seed=self.seed,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            self.client._client_kwargs.update(timeout=90, max_retries=0)
        if self.deadline is not None:
            remaining = max(0.1, self.deadline - time.monotonic())
            # The client is created lazily by the adapter.
            self.client._client_kwargs["timeout"] = min(90, remaining)
        started = time.monotonic()
        print(f"LOCAL_QWEN request={key[:12]} max_tokens={token_limit}", flush=True)
        request_kwargs = dict(kwargs)
        request_kwargs.setdefault("max_tokens", token_limit)
        if self.deadline is not None:
            request_kwargs["timeout"] = max(0.1, min(90, self.deadline - time.monotonic()))
        answer = self.client(
            messages, one_line=one_line, json_mode=json_mode, **request_kwargs,
        )
        elapsed = time.monotonic() - started
        self.api_calls += 1
        self.actual_seconds += elapsed
        self.input_token_count = self.client.input_token_count
        self.output_token_count = self.client.output_token_count
        self.input_token_maxx = self.client.input_token_maxx
        save(path, {"spec": spec, "answer": answer, "elapsed_seconds": elapsed})
        return answer


class SnapshotReady(Exception):
    pass


class CaptureAgent(ExperimentAgent):
    def reranking_attractions_with_constraints(self, plan, poi_type, current_day,
                                               current_time, current_position,
                                               attr_info, query, ranking_attractions):
        if not self.attractions_visiting:
            self.capture = {
                "query": freeze(query),
                "poi_plan": {k: freeze(v.to_dict() if hasattr(v, "to_dict") else v)
                             for k, v in self._context[1].items()},
                "plan": freeze(plan), "current_day": current_day,
                "current_time": current_time, "current_position": current_position,
            }
            raise SnapshotReady()
        return super().reranking_attractions_with_constraints(
            plan, poi_type, current_day, current_time, current_position,
            attr_info, query, ranking_attractions,
        )


def prepare(args):
    from chinatravel.data.load_datasets import ORACLE_FIELDS
    from chinatravel.environment.world_env import WorldEnv
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "frozen.json"
    if path.exists():
        raise FileExistsError("Frozen state already exists; choose a new --out")
    query_path = PROJECT / "experiments/c_only/queries" / f"{args.case}.json"
    full_query = read(query_path)
    query = {k: v for k, v in full_query.items() if k not in ORACLE_FIELDS}
    assert not ORACLE_FIELDS.intersection(query)
    deadline = time.monotonic() + args.seconds
    llm = CachedLocalLLM(output / "llm_cache", args.seed, deadline)
    agent = CaptureAgent(
        group="C", env=WorldEnv(lang="zh"), backbone_llm=llm,
        cache_dir=str(output / "translation_cache"), debug=False, lang="zh",
        wall_limit_seconds=args.seconds,
    )
    agent.soft_started = time.monotonic()
    log_path = output / "prepare.log"
    if log_path.exists():
        archived = output / ("prepare_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".log")
        log_path.rename(archived)
    with log_path.open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            translated = agent.translate_nl2sl(query, load_cache=False)
            try:
                agent.symbolic_search(translated)
            except SnapshotReady:
                pass
            if not hasattr(agent, "capture"):
                raise RuntimeError("No first-attraction decision reached; inspect prepare.log")
            state = agent.capture
            # Both recommendation lists are sampled once, then frozen in all arms.
            agent.ranking_restaurants(
                state["plan"], state["poi_plan"], state["current_day"],
                state["current_time"], state["current_position"],
                agent.intercity_with_hotel_cost,
            )
    state["agent"] = {name: freeze(getattr(agent, name)) for name in STATE_FIELDS}
    state["memory"] = {
        name: freeze(frame.to_dict(orient="split"))
        for name, frame in agent.memory.items()
    }
    snapshot = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "case": args.case, "uid": query["uid"], "seed": args.seed,
        "oracle_in_planning": False, "state": state,
        "source_sha256": {name: digest(PROJECT / name) for name in SOURCES},
        "source_query_sha256": digest(query_path),
        "model": llm.name, "prepare_api_calls": llm.api_calls,
        "prepare_cache_hits": llm.cache_hits,
        "prepare_format_errors": agent.llm_rec_format_error,
        "prepare_llm_seconds": llm.actual_seconds,
        "hotel_request_max_tokens": 2048,
        "scope": "one real pre-first-attraction state; fixed translation/tickets/hotel/recommendations",
    }
    save(path, snapshot)
    print(f"Frozen state: {path}")
    print(f"Preparation: {llm.api_calls} local Qwen calls, {llm.actual_seconds:.1f}s inference")


def restore(snapshot, agent):
    import pandas as pd
    state = snapshot["state"]
    for name, value in state["agent"].items():
        setattr(agent, name, copy.deepcopy(value))
    agent.memory = {name: pd.DataFrame(**data) for name, data in state["memory"].items()}
    agent.ranking_attractions_flag = agent.ranking_restaurants_flag = True
    for name in COUNTERS:
        setattr(agent, name, 0)
    agent.prune_counts, agent.prune_examples = {}, {}
    agent.least_plan_schema = agent.least_plan_comm = agent.least_plan_logic = None
    agent.least_plan_logical_pass = -1
    agent.preference_search = False
    agent.time_before_search = time.time()
    agent.soft_started = agent.started = time.monotonic()
    return copy.deepcopy({
        k: state[k] for k in (
            "query", "poi_plan", "plan", "current_time", "current_position", "current_day",
        )
    })


def check_sources(snapshot):
    changed = [name for name, expected in snapshot["source_sha256"].items()
               if digest(PROJECT / name) != expected]
    if changed:
        raise ValueError("Sources changed since prepare; make a new frozen state: " + str(changed))


def run_one(args, variant):
    from chinatravel.environment.world_env import WorldEnv
    from rules import audit
    output = args.out.resolve()
    snapshot = read(output / "frozen.json")
    check_sources(snapshot)
    label = args.label or f"{args.activity_policy}_n{args.nodes}_c{args.candidates}"
    if Path(label).name != label or label in {".", ".."}:
        raise ValueError("--label must be a directory name")
    directory = output / label / variant
    if directory.exists():
        raise FileExistsError(f"Refusing to overwrite run: {directory}")
    directory.mkdir(parents=True)
    llm = CachedLocalLLM(output / "llm_cache", snapshot["seed"])
    agent = PrefixProbeAgent(
        variant=variant, dfs_budget=args.nodes, activity_policy=args.activity_policy,
        event_path=directory / "events.jsonl", env=WorldEnv(lang="zh"),
        backbone_llm=llm, cache_dir=str(directory / "cache"), debug=False, lang="zh",
        candidate_limit=args.candidates or sys.maxsize, wall_limit_seconds=args.seconds,
    )
    agent.on_candidate = agent.candidate_found
    state = restore(snapshot, agent)
    llm.deadline = agent.started + args.seconds
    error = None
    reason = "search_exhausted"
    print(f"Running {variant}: DFS budget={args.nodes}, wall limit={args.seconds}s", flush=True)
    with (directory / "search.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                agent.dfs_poi(**state)
            except CandidateCollectionComplete:
                reason = "candidate_limit_reached"
            except CandidateDeadline:
                reason = "wall_limit_reached"
            except ProbeBudget as exc:
                reason = str(exc)
            except Exception as exc:
                reason = "error"
                error = {"type": type(exc).__name__, "message": str(exc)}
                import traceback
                traceback.print_exc()
    winner = agent.pool.best()
    elapsed = time.monotonic() - agent.started
    best_score = winner["score"] if winner else None
    result = {
        "case": snapshot["case"], "uid": snapshot["uid"], "variant": variant,
        "activity_policy": args.activity_policy, "scope": snapshot["scope"],
        "frozen_sha256": digest(output / "frozen.json"),
        "dfs_budget": args.nodes, "wall_limit_seconds": args.seconds,
        "candidate_limit": args.candidates or None, "stop_reason": reason,
        "error": error, "candidate_count": len(agent.pool.items),
        "found_valid_candidate": winner is not None,
        "best_penalty": best_score["total_penalty"] if best_score else None,
        "score_unknown_count": len(best_score["unknown"]) if best_score else None,
        "score_coverage": best_score["coverage"] if best_score else None,
        "selected_C_audit": audit(winner["plan"], agent.query["days"]) if winner else None,
        "elapsed_seconds": elapsed, "dfs_entries": agent.dfs_entries,
        "counters": {name: getattr(agent, name) for name in COUNTERS},
        "llm_api_calls": llm.api_calls, "llm_cache_hits": llm.cache_hits,
        "actual_llm_seconds": llm.actual_seconds,
        "non_llm_seconds": max(0, elapsed - llm.actual_seconds),
        "first_candidate": agent.discovery_curve[0] if agent.discovery_curve else None,
        "diversity": diversity(agent.pool.items),
        "first_rankings": agent.first_rankings,
        "branches": agent.branches, "discovery_curve": agent.discovery_curve,
        "prune_counts": agent.prune_counts,
        "prune_depths": [
            {"rule": rule, "activity_depth": depth, "count": count}
            for (rule, depth), count in sorted(agent.prune_depths.items())
        ],
        "validation_depths": [
            {"event": event, "activity_depth": depth, "count": count}
            for (event, depth), count in sorted(agent.validation_depths.items())
        ],
    }
    save(directory / "summary.json", result)
    save(directory / "candidates.json", agent.pool.items)
    save(directory / "selected.json", winner["plan"] if winner else {})
    print(f"{variant}: {reason}, candidates={len(agent.pool.items)}, "
          f"best={result['best_penalty']}, DFS={agent.dfs_entries}, {elapsed:.1f}s")
    return result


def report(args):
    output = args.out.resolve()
    label = args.label or f"{args.activity_policy}_n{args.nodes}_c{args.candidates}"
    paths = [output / label / v / "summary.json" for v in VARIANTS]
    rows = [read(path) for path in paths if path.exists()]
    if not rows:
        raise ValueError("No summaries found")
    if len({(r["frozen_sha256"], r["dfs_budget"], r["wall_limit_seconds"],
             r["candidate_limit"], r["activity_policy"]) for r in rows}) != 1:
        raise ValueError("Mismatched experiment conditions")
    lines = [
        "# EXP001：早期地点选择敏感性",
        "", f"案例：{rows[0]['case']}；活动策略：{rows[0]['activity_policy']}。",
        f"每组 DFS 入口预算 {rows[0]['dfs_budget']}，时间上限 {rows[0]['wall_limit_seconds']} 秒；"
        f"候选上限 {rows[0]['candidate_limit'] or '仅受搜索预算限制'}。", "",
        "| 组别 | 停止原因 | 合法候选数 | 最优软惩罚↓ | DFS入口 | 搜索节点计数 | 不同首景点数 | 首景点最大占比 | 耗时秒 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        share = row["diversity"]["dominant_prefix_1_share"]
        share_text = f"{share:.1%}" if share is not None else "—"
        lines.append(
            f"| {row['variant']} | {row['stop_reason']} | {row['candidate_count']} | "
            f"{row['best_penalty'] if row['best_penalty'] is not None else '—'} | "
            f"{row['dfs_entries']} | {row['counters']['search_nodes']} | "
            f"{row['diversity']['unique_prefix_1']} | {share_text} | {row['elapsed_seconds']:.2f} |"
        )
    comparable = [r for r in rows if r["variant"] != "original" and r["best_penalty"] is not None]
    lines += ["", "## 自动观察", ""]
    for row in rows:
        starts = sorted({b["first_attraction"] for b in row["branches"]})
        first = row["first_candidate"]
        lines.append(
            f"- {row['variant']}：实际进入首景点分支 {starts or '无'}；"
            f"首次合法候选的 DFS 入口数 {first['dfs_entries'] if first else '未找到'}；"
            f"评分未知项 {row['score_unknown_count']}。"
        )
        if row["error"]:
            lines.append(f"  - 运行错误：{row['error']}。本组不作为算法失败率样本。")
    if len(comparable) > 1:
        penalties = [r["best_penalty"] for r in comparable]
        lines.append(
            f"- 修正排序后的不同开头干预组，已找到方案的最优惩罚跨度为 "
            f"{max(penalties) - min(penalties):.6f}；无候选组不填成零分。"
        )
    lines += [
        "", "## 解释边界", "",
        "- 这是一个真实搜索状态的受控短实验；交通、酒店、翻译和推荐列表冻结，不代表完整端到端评测。",
        "- rules 模式在所有组统一使用确定性活动类型规则，用来隔离地点排序；qwen 模式使用原 C 组活动决策。",
        "- original 对照 rank_fixed 检查排序实现影响；rank_fixed 对照 second_first / third_first 检查早期地点影响。",
        "- 干预只把候选第二/第三位提前，其余候选保留；提前的地点仍可能因实际检查被拒绝，应以 branches 为准。",
        "- DFS入口预算与原 search_nodes 分开报告；后者还累计全表重排和交通尝试，不能混用。",
        "- 首景点分支的统计从加入该地点后的递归入口开始；父节点排序和加入前检查计入总体，未归入分支。",
        "- 20条候选可能提前停止；需用 --candidates 0 的预算停止实验复核。",
        "- Qwen模式共享精确提示词缓存，实际耗时受命中率影响；优先比较节点预算结果并查看模型调用数。",
        "- 合法指原约束检查和 C 组审计通过。翻译正确性、用户满意度及跨案例普遍性仍需独立验证。",
        "- 单例结果不足以断言主要耗时来源；后续至少覆盖多个城市、约束复杂度和独立需求。",
        "",
    ]
    destination = output / label / "REPORT.md"
    destination.write_text("\n".join(lines), encoding="utf-8")
    save(output / label / "comparison.json", rows)
    print(f"Report: {destination}")


def main():
    os.chdir(PROJECT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "report"))
    parser.add_argument("--case", default="easy_1")
    parser.add_argument("--out", type=Path, default=PROJECT / "artifacts/prefix_probe/EXP001_easy_1")
    parser.add_argument("--seed", type=int, default=20261109)
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--nodes", type=int, default=200)
    parser.add_argument("--candidates", type=int, default=20,
                        help="0 means stop only on the node/time budget")
    parser.add_argument("--activity-policy", choices=("rules", "qwen"), default="rules")
    parser.add_argument("--variant", choices=(*VARIANTS, "all"), default="all")
    parser.add_argument("--label")
    args = parser.parse_args()
    if args.nodes < 1 or args.seconds <= 0 or args.candidates < 0:
        parser.error("nodes/seconds must be positive; candidates must be non-negative")
    if args.command == "prepare":
        prepare(args)
    elif args.command == "run":
        results = [run_one(args, variant) for variant in (
            VARIANTS if args.variant == "all" else (args.variant,)
        )]
        report(args)
        if any(result["error"] for result in results):
            raise SystemExit(1)
    else:
        report(args)


if __name__ == "__main__":
    main()
