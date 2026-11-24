"""Compare official DeepSeek API latency against frozen EXP028 requests."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_error(error):
    # Error bodies from a remote endpoint are not persisted with credentials.
    return {"type": type(error).__name__, "http_status": getattr(error, "status_code", None)}


def list_check(content, frozen):
    match = re.search(re.escape(frozen["field"]) + r":\s*(\[[^\]]*\])", content)
    try:
        values = ast.literal_eval(match.group(1)) if match else None
    except (SyntaxError, ValueError):
        values = None
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        return {"parsed": False}
    allowed = set(frozen["allowed_values"])
    local_text = ""
    historical = ROOT / "artifacts/product/EXP028_day_sets/paired/easy_1__baseline/cache/LLMNeSy_qwen3.5-4b-q8/t20241206164116321945.log"
    text = historical.read_text(encoding="utf-8")
    markers = list(re.finditer(r"^Thought:", text, re.M))
    start = {"outbound_ranking": markers[0].start(), "hotel_ranking": markers[1].start(),
             "return_ranking": markers[3].start()}[frozen["phase"]]
    local_match = re.search(re.escape(frozen["field"]) + r":\s*(\[[^\]]*\])", text[start:])
    if local_match:
        local_text = local_match.group(1)
    local = ast.literal_eval(local_text) if local_text else []
    return {"parsed": True, "count": len(values), "all_ids_valid": all(v in allowed for v in values),
            "duplicates": len(values) - len(set(values)), "top_5": values[:5],
            "overlap_with_local_top_5": len(set(values[:5]) & set(local[:5])),
            "same_list_as_local": values == local}


def bench(args):
    from openai import OpenAI

    key = os.environ["DEEPSEEK_API_KEY"]
    requests = read(args.frozen)
    assert all(row["local_hash_matches"] for row in requests)
    args.out.mkdir(parents=True, exist_ok=False)
    client = OpenAI(api_key=key, base_url="https://api.deepseek.com", timeout=90, max_retries=0)
    save(args.out / "MANIFEST.json", {
        "model": "deepseek-flash", "endpoint": "https://api.deepseek.com",
        "thinking": "disabled", "temperature": 0, "max_tokens": 4096,
        "repeats": args.repeats, "mode": "sequential nonstream",
        "frozen": str(args.frozen), "frozen_sha256": hashlib.sha256(args.frozen.read_bytes()).hexdigest(),
        "seed_sent": False, "timeout_seconds": 90,
        "comparison": "Same messages as EXP028, different model/provider/hardware and no API seed",
    })
    observed = []
    for repetition in range(1, args.repeats + 1):
        for frozen in requests:
            label = f"{repetition}_{frozen['phase']}"
            save(args.out / "STATUS.json", {"repetition": repetition, "phase": frozen["phase"], "completed": len(observed)})
            print(f"START {label}", flush=True)
            started = time.perf_counter()
            row = {"repetition": repetition, "phase": frozen["phase"]}
            try:
                result = client.chat.completions.create(
                    model="deepseek-flash", messages=frozen["messages"], temperature=0, max_tokens=4096,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                row.update(seconds=time.perf_counter() - started, status="complete", model=result.model,
                           finish_reason=result.choices[0].finish_reason, usage=result.usage.model_dump())
                content = result.choices[0].message.content or ""
                (args.out / f"{label}.txt").write_text(content, encoding="utf-8")
                row["list_check"] = list_check(content, frozen)
            except Exception as error:
                row.update(seconds=time.perf_counter() - started, status="error", error=safe_error(error))
            observed.append(row)
            save(args.out / "RESULTS.json", observed)
            print(f"DONE {label} {row['seconds']:.2f}s {row['status']} {row.get('list_check', {})}", flush=True)
            if row.get("error", {}).get("http_status") in {401, 402, 403}:
                save(args.out / "STATUS.json", {"stopped": True, "reason": row["error"], "completed": len(observed)})
                return
    summary = {}
    for frozen in requests:
        successful = [r for r in observed if r["phase"] == frozen["phase"] and r["status"] == "complete"]
        summary[frozen["phase"]] = {
            "successful_calls": len(successful), "local_seconds": frozen["local_request"]["seconds"],
            "local_output_tokens": frozen["local_request"]["output_tokens"],
            "api_seconds": [r["seconds"] for r in successful],
            "api_median_seconds": statistics.median(r["seconds"] for r in successful) if successful else None,
            "api_output_tokens": [r["usage"]["completion_tokens"] for r in successful],
            "all_lists_valid": all(r["list_check"].get("all_ids_valid", False) for r in successful),
        }
    save(args.out / "SUMMARY.json", summary)
    save(args.out / "STATUS.json", {"finished": True, "completed": len(observed)})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def full_plan(args):
    from copy import deepcopy
    from types import SimpleNamespace
    sys.path[:0] = [str(ROOT / "experiments/timing_probe"), str(ROOT / "experiments/c_only")]
    from chinatravel.environment.sandbox.__main__ import configuration
    os.environ.update(configuration(args.data))
    import chinatravel.agent.llms as backend
    key = os.environ["DEEPSEEK_API_KEY"]

    def api_model(*_args, **_kwargs):
        # The legacy profiler configures a local endpoint before this factory.
        os.environ["CHINATRAVEL_OPENAI_BASE_URL"] = "https://api.deepseek.com"
        model = backend.OpenAICompatibleLLM(
            "deepseek-flash", name="deepseek-flash", base_url="https://api.deepseek.com", api_key=key,
            max_tokens=4096, token_limit_arg="max_tokens", wire_api="chat",
            default_request_args={"temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}},
        )
        from chinatravel.personalization.lessons import LessonBook
        model._client_kwargs.update(LessonBook().model_client_options())
        original_send = model._send_request
        def send(messages, kwargs):
            actual = dict(kwargs)
            actual.pop("seed", None)
            return original_send(messages, actual)
        model._send_request = send
        return model

    backend.create_llm = api_model
    if args.freeze_dsl:
        from chinatravel.agent.nesy_agent.nesy_agent import NesyAgent
        old = ROOT / "artifacts/product/EXP028_day_sets/paired" / (args.query.stem + "__day_sets")
        generated = read(next(old.glob("cache/translation_*/*.json")))
        def frozen_translation(_agent, _query, load_cache=False):
            return deepcopy(generated)
        NesyAgent.translate_nl2sl = frozen_translation
    import run_activity_granularity as probe
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save(args.out.parent / (args.out.name + "_API.json"), {
        "model": "deepseek-flash", "thinking": "disabled", "seed_sent": False,
        "budget_seconds": args.seconds, "candidate_limit": getattr(args, 'candidate_limit', 20), "time_policy": args.time_policy,
        "frozen_dsl": args.freeze_dsl,
        "description": "Real natural-language request, all model calls use the official API",
    })
    probe.measure(SimpleNamespace(
        arm=args.arm, query=args.query,
        out=args.out, seconds=args.seconds, endpoint="https://api.deepseek.com", time_policy=args.time_policy,
        candidate_limit=getattr(args, 'candidate_limit', 20),
    ))
    if not args.defer_audit:
        result = probe.audit(args.out, read(args.query), args.time_policy)
        print(json.dumps(result, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bench", "full"))
    parser.add_argument("--frozen", type=Path, default=ROOT / "artifacts/product/EXP030_api_speed/FROZEN_REQUESTS.json")
    parser.add_argument("--data", type=Path, default=ROOT.parent / "ShortTrip/artifacts/data/5e0ee8a")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--freeze-dsl", action="store_true")
    parser.add_argument("--query",type=Path,default=ROOT / "experiments/c_only/queries/easy_1.json")
    parser.add_argument("--arm",choices=("baseline","day_sets","combo_schedule","personalized"),default="day_sets")
    parser.add_argument("--candidate-limit",type=int,default=20)
    parser.add_argument("--time-policy",choices=("strict","flexible"),default="strict")
    parser.add_argument("--seconds",type=float,default=240)
    parser.add_argument("--defer-audit",action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("Set DEEPSEEK_API_KEY in this process before running.")
    if args.repeats < 1:
        raise SystemExit("repeats must be positive")
    try:
        if args.command == "bench":
            bench(args)
        else:
            full_plan(args)
    except Exception as error:
        print(json.dumps({"failed": safe_error(error)}), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

