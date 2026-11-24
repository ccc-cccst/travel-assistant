"""Profile fresh ShortTrip cases and compare ranking prompts on paired cases."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from live_suite import PROJECT, digest, measure


ARTIFACTS = PROJECT / "artifacts/live_timing"
PILOT = ARTIFACTS / "EXP010_original_dfs_1794462162344/run"
DATASET = ARTIFACTS / "EXP009_full_1794461635758/run/queries"
FRESH = ("easy_025", "medium_038", "human_038", "easy_175", "medium_113", "human_088")
PAIRS = ("easy_3", "medium_1", "human_2")


def write(path, value):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18086)
    parser.add_argument("--seconds", type=float, default=360)
    parser.add_argument("--phase", choices=("fresh", "pairs", "all"), default="all")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    queries = out / "queries"
    queries.mkdir()
    source = PILOT / "sources/shorttrip"
    schedule = []
    if args.phase != "pairs":
        schedule.extend((case, "observed") for case in FRESH)
    if args.phase != "fresh":
        for index, case in enumerate(PAIRS):
            order = ("baseline", "concise") if index % 2 == 0 else ("concise", "baseline")
            schedule.extend((case, variant) for variant in order)
    for case in dict.fromkeys(case for case, _ in schedule):
        directory = PILOT / "queries" if case in PAIRS else DATASET
        shutil.copy2(directory / f"{case}.json", queries)

    write(out / "manifest.json", {
        "source": str(source), "baseline_manifest": str(PILOT / "manifest.json"),
        "query_sha256": {p.stem: digest(p) for p in sorted(queries.glob("*.json"))},
        "schedule": schedule, "seconds": args.seconds, "max_tokens": 4096,
        "original_stops": True, "seed": 20261112, "temperature": 0,
        "harness_sha256": {name: digest(Path(__file__).with_name(name)) for name in (
            "speed_study.py", "spans.py", "live_profile.py", "live_suite.py")},
    })
    status = {"current": None, "finished": [], "failed": []}
    try:
        for case, variant in schedule:
            task = {"case": case, "variant": variant}
            status["current"] = task
            write(out / "STATUS.json", status)
            settings = SimpleNamespace(out=out / variant, port=args.port, seconds=args.seconds,
                                       max_tokens=4096, original_stops=True,
                                       concise_ranking=variant == "concise")
            try:
                measure(settings, "shorttrip", case, source, queries / f"{case}.json")
                run = json.loads((settings.out / case / "shorttrip/profile/run.json").read_text("utf-8"))
                task.update(stop_reason=run["stop_reason"], seconds=run["wall_seconds"],
                            candidates=run["candidate_count"])
                if run["error"]:
                    raise RuntimeError(str(run["error"]))
            except Exception as error:
                status["failed"].append({**task, "error": str(error)})
                print(case, variant, str(error), flush=True)
            else:
                status["finished"].append(task)
    finally:
        status.update(current=None, ended_at=datetime.now(timezone.utc).isoformat(),
                      complete=len(status["finished"]) + len(status["failed"]) == len(schedule))
        write(out / "STATUS.json", status)


if __name__ == "__main__":
    main()
