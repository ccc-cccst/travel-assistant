"""Profile one live, isolated run of the ShortTrip or ChinaTravel LLMNeSy agent."""

import argparse
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

from spans import ExclusiveTimer


RANKS = {
    "prepare_trip_recall": "trip_candidate_recall",
    "ranking_intercity_transport_go": "outbound_ranking",
    "ranking_intercity_transport_back": "return_ranking",
    "ranking_hotel": "hotel_ranking",
    "ranking_attractions": "attraction_ranking",
    "ranking_restaurants": "restaurant_ranking",
    "ranking_innercity_transport_from_query": "local_mode_ranking",
    "decide_rooms": "room_inference",
    "extract_budget": "budget_inference",
    "select_next_poi_type": "activity_type",
    "select_poi_time": "visit_duration",
    "build_day_options": "day_sets",
}
REQUEST_CONTEXTS = {"translation", *RANKS.values()}
RANKING_FIELDS = {
    "outbound_ranking": "IDList", "return_ranking": "IDList",
    "hotel_ranking": "HotelNameList", "attraction_ranking": "AttractionNameList",
    "restaurant_ranking": "RestaurantNameList",
}


class RunDeadline(BaseException):
    pass


def configure(endpoint):
    os.environ.pop("CHINATRAVEL_ZH_DATABASE", None)
    os.environ.update(
        CHINATRAVEL_OPENAI_BASE_URL=endpoint, CHINATRAVEL_OPENAI_API_KEY="local-no-key",
        CHINATRAVEL_OPENAI_MODEL="qwen3.5-4b-q8", CHINATRAVEL_OPENAI_WIRE_API="chat",
        CHINATRAVEL_OPENAI_TOKEN_LIMIT_ARG="max_tokens", CHINATRAVEL_OPENAI_RAISE_ERRORS="1",
        NO_PROXY="127.0.0.1,localhost",
    )


def run(args):
    timer = ExclusiveTimer()
    started = time.perf_counter()
    cpu_started = time.process_time()
    deadline = started + args.seconds
    args.out.mkdir(exist_ok=False)
    configure(args.endpoint)
    sys.path.insert(0, str(args.source))
    if args.arm == "shorttrip":
        sys.path.insert(0, str(args.source / "experiments/c_only"))
    with timer.span("imports"):
        import chinatravel.agent.nesy_agent.nesy_agent as nesy
        from chinatravel.agent.llms import create_llm
        from chinatravel.agent.nesy_agent.llm_driven_rec import LLMDrivenAgent
        from chinatravel.agent.utils import Logger, NpEncoder
        from chinatravel.data.load_datasets import ORACLE_FIELDS
        from chinatravel.environment.world_env import WorldEnv
        if args.arm == "shorttrip":
            import experiment_agent as c
            from chinatravel.soft_constraints.pool import CandidatePool

    def save(name, value):
        with timer.span("result_io"):
            (args.out / name).write_text(json.dumps(
                value, ensure_ascii=False, indent=2, cls=NpEncoder,
            ), encoding="utf-8")

    def check_budget():
        if time.perf_counter() >= deadline:
            raise RunDeadline()

    def guard(obj, name):
        original = getattr(obj, name)

        @functools.wraps(original)
        def guarded(*a, **kw):
            check_budget()
            return original(*a, **kw)

        setattr(obj, name, guarded)

    first_seconds = None

    def first_result(plan):
        nonlocal first_seconds
        if first_seconds is None:
            check_budget()
            save("first_plan.json", plan)
            first_seconds = time.perf_counter() - started

    full_query = json.loads(args.query.read_text(encoding="utf-8"))
    query = {key: value for key, value in full_query.items() if key not in ORACLE_FIELDS}
    query['time_policy']=getattr(args,'time_policy','strict')
    with timer.span("environment_and_model_setup"):
        env = WorldEnv(lang="zh")
        llm = create_llm("qwen3.5-4b-q8", max_tokens=args.max_tokens)
        llm._client_kwargs.update(timeout=600, max_retries=0)
        settings = dict(env=env, backbone_llm=llm, cache_dir=str(args.out / "cache"),
                        debug=False, lang="zh")
        if args.arm == "shorttrip":
            agent = c.ExperimentAgent(group="C", candidate_limit=getattr(args, "candidate_limit", 20),
                                      wall_limit_seconds=1750 if args.original_stops else None,
                                      on_candidate=lambda pool: first_result(pool.items[-1]["plan"]),
                                      **settings)
        else:
            agent = LLMDrivenAgent(**settings)
        if not args.original_stops:
            # The short paired probe uses a shared cutoff instead of internal clocks.
            agent.TIME_CUT = float("inf")

    request_number = 0
    original_send = llm._send_request
    experiment = None
    if args.preflight or args.return_batch_size:
        if args.arm != "shorttrip":
            raise ValueError("Return intervention requires the ShortTrip C group")
        from rsi_transport import ReturnExperiment
        experiment = ReturnExperiment(agent, args.preflight, args.return_batch_size, check_budget, args.adaptive_rescue)

    def send(messages, request_args):
        nonlocal request_number
        check_budget()
        request_number += 1
        phase = next((name for name, *_ in reversed(timer.stack) if name in REQUEST_CONTEXTS), "other")
        request_start = time.perf_counter()
        kwargs = dict(request_args)
        remaining = max(.01, deadline - request_start)
        kwargs.update(timeout=min(600, remaining) if args.original_stops else remaining,
                      temperature=0, seed=args.seed)
        kwargs["max_tokens"] = min(args.max_tokens, kwargs.get("max_tokens", args.max_tokens))
        if hasattr(agent, 'limit_model_request'):
            kwargs = agent.limit_model_request(kwargs)
        field = RANKING_FIELDS.get(phase)
        compact_poi = (args.adaptive_rescue and experiment is not None
                       and (experiment.outbound_format_failed or experiment.hotel_rescued)
                       and phase in {"attraction_ranking", "restaurant_ranking"})
        if compact_poi:
            kwargs["max_tokens"] = min(kwargs["max_tokens"], 640)
            experiment.events.append({"event": "compact_poi", "phase": phase})
        if args.concise_ranking and field:
            messages = [*messages[:-1], {**messages[-1], "content": messages[-1]["content"] + (
                f"\nOutput only `{field}:` followed by the requested list. "
                "Keep the ranking criteria and list requirements above. Omit Thought, explanations and Markdown."
            )}]
        elif compact_poi:
            messages = [*messages[:-1], {**messages[-1], "content": messages[-1]["content"] + (
                f"\nReturn only `{field}:` and a list of exact names already in the table. "
                "Keep any budget for remaining meals and travel. Omit reasoning and Markdown."
            )}]
        request_key = json.dumps([messages, {k: v for k, v in kwargs.items() if k != "timeout"}],
                                 ensure_ascii=False, sort_keys=True)
        record = {"call": request_number, "phase": phase, "started_seconds": request_start - started,
                  "request_sha256": hashlib.sha256(request_key.encode("utf-8")).hexdigest(),
                  "max_tokens": kwargs["max_tokens"], "prompt_chars": sum(len(str(m["content"])) for m in messages)}
        tokens_before = llm.output_token_count
        try:
            with timer.span(f"llm/{phase}"):
                answer = original_send(messages, kwargs)
            check_budget()
            if os.environ.get('SHORTTRIP_RANK_CONTROL'):
                from rank_replay import control_answer
                answer, control = control_answer(answer,phase,record['request_sha256'],os.environ['SHORTTRIP_RANK_CONTROL'])
                if control is not None:record['rank_control']=control
            record["status"] = "complete"
            if field:
                content = answer.get("content")
                match = re.search(rf"{field}:\s*(\[[^\]]*\])", content) if isinstance(content, str) else None
                record["has_ranking_list"] = match is not None
                record["ranking_chars"] = len(match.group(1)) if match else 0
            return answer
        except Exception as error:
            record["status"] = type(error).__name__
            if time.perf_counter() >= deadline - .1:
                raise RunDeadline() from error
            raise
        except RunDeadline:
            record["status"] = "deadline"
            raise
        finally:
            record["seconds"] = time.perf_counter() - request_start
            record["output_tokens"] = llm.output_token_count - tokens_before
            with timer.span("result_io"):
                with (args.out / "requests.jsonl").open("a", encoding="utf-8") as out:
                    out.write(json.dumps(record) + "\n")

    llm._send_request = send
    for method, label in {
        **RANKS, "translate_nl2sl": "translation", "symbolic_search": "symbolic_search",
        "generate_plan_with_search": "outer_search", "dfs_poi": "dfs",
        "collect_innercity_transport": "route_lookup",
        "collect_intercity_transport": "intercity_db", "collect_poi_info_all": "poi_db",
        "constraints_validation": "full_validation", "prune_partial": "partial_pruning",
        "missing_today": "meal_windows", "meals_possible_after": "meal_lookahead",
        **{f"reranking_{kind}_with_constraints": "constraint_reranking" for kind in (
            "attractions", "restaurants", "intercity_transport_go", "intercity_transport_back", "hotel")},
    }.items():
        if hasattr(agent, method):
            timer.wrap(agent, method, label)
    transport = type(env.transportation)
    timer.wrap(transport, "goto", "route_compute")
    if not args.original_stops:
        guard(transport, "goto")
        guard(agent, "dfs_poi")
    timer.wrap(Logger, "write", "planner_log_write")
    timer.wrap(nesy, "func_commonsense_constraints", "commonsense_validation")
    timer.wrap(nesy, "evaluate_constraints_py", "dsl_validation")
    timer.wrap(nesy, "get_symbolic_concepts", "symbolic_concepts")
    if args.arm == "shorttrip":
        timer.wrap(c, "audit", "c_rule_audit")
        timer.wrap(agent, "_score", "soft_scoring")
        timer.wrap(CandidatePool, "add", "candidate_pool")
        timer.wrap(CandidatePool, "best", "candidate_pool")
    else:
        validation = agent.constraints_validation

        def capture_native(*a, **kw):
            success, plan = validation(*a, **kw)
            if success:
                first_result(plan)
            return success, plan

        agent.constraints_validation = capture_native

    success, plan, error = False, {}, None
    reason = "finished"
    stdout, stderr = sys.stdout, sys.stderr
    try:
        with timer.span("planning"):
            success, plan = agent.run(query, load_cache=False)
    except RunDeadline:
        reason = "matched_wall_budget"
        if args.arm == "shorttrip" and agent.pool.best():
            success, plan = True, agent.pool.best()["plan"]
    except Exception as exc:
        reason = "error"
        error = {"type": type(exc).__name__, "message": str(exc)}
        traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    if args.arm == "shorttrip" and reason == "finished":
        reason = agent.selection["stop_reason"]

    with timer.span("output"):
        save("plan.json", plan)
        if args.arm == "shorttrip":
            save("pool.json", agent.pool.items)
        if experiment is not None:
            save("transport_events.json", experiment.events)
        if hasattr(agent, "selection"):
            save("selection.json", agent.selection)
    wall = time.perf_counter() - started
    stats = dict(timer.stats)
    save("run.json", {"arm": args.arm, "query": str(args.query), "uid": query["uid"],
                      "wall_seconds": wall, "cpu_seconds": time.process_time() - cpu_started,
                      "budget_seconds": args.seconds, "stop_reason": reason, "error": error,
                      "time_policy":query.get('time_policy','strict'),
                      "original_stops": args.original_stops,
                      "concise_ranking": args.concise_ranking,
                      "native_time_out_flag": plan.get("time_out_flag") if args.arm == "native" else None,
                      "first_published_seconds": first_seconds, "internal_success": success,
                      "candidate_count": len(agent.pool.items) if args.arm == "shorttrip" else int(first_seconds is not None),
                      "request_count": request_number, "output_tokens": llm.output_token_count,
                      "translation_fallback": getattr(agent, "translation_fallback", None),
                      "search_nodes": getattr(agent, "search_nodes", None),
                      "prune_counts": getattr(agent, "prune_counts", {}),
                      "stats": stats, "unattributed_seconds": wall - sum(s["exclusive_seconds"] for s in stats.values())})
    print(args.arm, args.query.stem, reason, f"{wall:.1f}s", f"first={first_seconds}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("shorttrip", "native"), required=True)
    for name in ("source", "query", "out"):
        parser.add_argument(f"--{name}", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--candidate-limit", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18084/v1")
    parser.add_argument("--original-stops", action="store_true")
    parser.add_argument("--concise-ranking", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--return-batch-size", type=int, choices=(0, 1, 2, 3, 4), default=0)
    parser.add_argument("--adaptive-rescue", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
