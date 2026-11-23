"""Run a tiered local comparison of return planning interventions."""

import argparse
from collections import Counter
import ctypes
import json
from pathlib import Path
import shutil
from statistics import median
from types import SimpleNamespace

from live_report import profile, read
from live_suite import PROJECT, digest, measure
from speed_report import details, table
from speed_study import PILOT, write


ROOT = PROJECT / "artifacts/live_timing"
BASELINE = ROOT / "EXP011_speed_20261114/baseline_live"
LEGACY = ROOT / "EXP008_all_tiers_1794457018569/run"
CASES = ("human_038", "easy_025", "medium_113", "human_088", "easy_225", "medium_013", "human_2")
MODES = {"baseline": (False, 0), "screen": (True, 0), "batch": (False, 3),
         "combined": (True, 3), "rescue": (True, 3)}


def comparison(out, results, reference):
    doc = ["# EXP013 ShortTrip 分层返程实验", "",
           "历史对照使用同输入、源码快照、模型配置、360 秒外层上限和原停止规则。"
           "human_2 的历史对照来自 EXP008，保留其协议差异，用作诊断。", ""]
    paired = [(case, reference[case], row) for case, row in results.items() if case in reference]
    for tier in ("easy", "medium", "human"):
        pairs = [p for p in paired if p[1]["tier"] == tier and p[0] != "human_2"]
        doc += [f"## {tier}", ""]
        if not pairs:
            continue
        doc += table(["已完成配对", "截止数 原→新", "选中合法 原→新", "时间中位差（秒）"], [(len(pairs),
                     f"{sum(old['stop']=='matched_wall_budget' for _,old,_ in pairs)}→{sum(new['stop']=='matched_wall_budget' for _,_,new in pairs)}",
                     f"{sum(legal(old) for _,old,_ in pairs)}→{sum(legal(new) for _,_,new in pairs)}",
                     median(new["seconds"] - old["seconds"] for _, old, new in pairs))])
    doc += ["## 逐条结果", ""]
    doc += table(["案例", "墙钟秒 原→新", "返程调用 原→新", "返程秒 原→新", "早排除", "首次共同审核秒 原→新",
                  "停止 原→新", "选中合法 原→新", "相同行程"],
                 ((case, f"{a['seconds']:.2f}→{b['seconds']:.2f}",
                   f"{phase(a)['calls']}→{phase(b)['calls']}", f"{phase(a)['seconds']:.2f}→{phase(b)['seconds']:.2f}",
                   b["screened"], f"{sec(a['first_seconds'])}→{sec(b['first_seconds'])}", f"{a['stop']}→{b['stop']}",
                   f"{legal(a)}→{legal(b)}", a.get("plan_fingerprint") == b.get("plan_fingerprint") if legal(a) and legal(b) else "—")
                  for case, a, b in paired))
    doc += table(["案例", "方案", "费用元", "交通分钟", "景点数", "非必要重访", "换酒店", "节奏分"],
                 ((case, group, *(r["quality"].get(k) for k in
                                   ("total_cost", "transport_minutes", "unique_attractions",
                                    "nonessential_revisits", "hotel_switches", "R")))
                  for case, a, b in paired for group, r in (("原", a), ("新", b)) if "quality" in r))
    doc += ["同样在时限内失败的案例不能作为质量通过。时限内无解结束与超时分别保留。"
            "已通过规则审核的行程才列质量指标。", ""]
    (out / "REPORT.md").write_text("\n".join(doc), encoding="utf-8")
    write(out / "RESULTS.json", {"reference": reference, "results": results})


def sec(value):
    return "—" if value is None else f"{value:.2f}"


def legal(row):
    return row["common_audit"] == "pass" and row["c_audit"] == "pass"


def phase(row):
    return row["phases"].get("return_ranking", {"calls": 0, "seconds": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--port", type=int, default=18087)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    screen, batch = MODES[args.mode]
    out = args.out.resolve()
    source = PILOT / "sources/shorttrip"
    queries = out / "queries"
    manifest = {"mode": args.mode, "cases": list(args.cases), "preflight": screen, "return_batch_size": batch,
                "seconds": 360, "seed": 20261112, "source": str(source),
                "harness_sha256": {name: digest(Path(__file__).with_name(name)) for name in (
                    "rsi_suite.py", "rsi_transport.py", "live_profile.py", "live_suite.py")}}
    for case in args.cases:
        ref = LEGACY if case == "human_2" else BASELINE
        manifest.setdefault("query_sha256", {})[case] = digest(ref / "queries" / f"{case}.json")
    if args.resume:
        if read(out / "manifest.json") != manifest:
            raise ValueError("Resume configuration differs from the saved run")
    else:
        queries.mkdir(parents=True, exist_ok=False)
        for case in args.cases:
            ref = LEGACY if case == "human_2" else BASELINE
            shutil.copy2(ref / "queries" / f"{case}.json", queries)
        write(out / "manifest.json", manifest)

    settings = SimpleNamespace(out=out / "runs", port=args.port, seconds=360, max_tokens=4096,
                               original_stops=True, preflight=screen, return_batch_size=batch,
                               adaptive_rescue=args.mode == "rescue")
    results, reference, status = {}, {}, {"state": "running", "current": None, "finished": [], "failures": []}
    power = ctypes.windll.kernel32
    power.SetThreadExecutionState(0x80000001)
    try:
        for case in args.cases:
            ref = LEGACY if case == "human_2" else BASELINE
            query_path = queries / f"{case}.json"
            query = read(query_path)
            ref_dir = ref / case / "shorttrip"
            reference[case] = details("reference", "baseline", case, "shorttrip", ref_dir, query, profile(ref_dir, query))
            status["current"] = case
            write(out / "STATUS.json", status)
            directory = settings.out / case / "shorttrip"
            target = directory / "profile/run.json"
            try:
                if not (args.resume and target.exists()):
                    measure(settings, "shorttrip", case, source, query_path)
                stats = profile(directory, query)
                if stats["error"]:
                    raise RuntimeError(str(stats["error"]))
                row = details("EXP013", args.mode, case, "shorttrip", directory, query, stats)
                event_path = directory / "profile/transport_events.json"
                events = read(event_path) if event_path.exists() else []
                row["transport_events"] = dict(Counter(e.get("reason", e["event"]) for e in events))
                row["screened"] = sum(e["event"] == "pruned" for e in events)
                results[case] = row
                status["finished"].append(case)
            except Exception as error:
                status["failures"].append({"case": case, "error": str(error)})
                print(case, error, flush=True)
            comparison(out, results, reference)
            write(out / "STATUS.json", status)
        status.update(state="completed", current=None)
    finally:
        if status["state"] != "completed":
            status["state"] = "interrupted"
        write(out / "STATUS.json", status)
        power.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
