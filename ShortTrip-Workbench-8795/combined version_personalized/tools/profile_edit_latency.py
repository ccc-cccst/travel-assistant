"""Measure preview setup and verified scheduling on frozen local archives."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SHANGHAI = ROOT / "result/workbench_edits/55350b07bb5546b1a8fbe374c6af0bc5/run"
CHENGDU = ROOT / "result/manual/7cfccaa570c744818c257c37a4256826"
SHANGHAI_ID = "943d6f07725c3c87313e8f57"
CHENGDU_ID = "6e08e84d0129a23b651bd0bc"
TIME = {"action": "time", "day": 3, "slot": 2, "start": "12:57", "end": "13:27"}
MEAL = {"action": "meal_options", "day": 3, "slot": 4, "cuisine": "当地特色菜"}

CASES = {
    "sh_time": (SHANGHAI, SHANGHAI_ID, TIME),
    "sh_lighter": (SHANGHAI, SHANGHAI_ID, {
        "action": "batch", "day": 3, "pace": "lighter", "pace_period": "afternoon", "operations": [TIME, MEAL]}),
    "sh_locked": (SHANGHAI, SHANGHAI_ID, {
        "action": "batch", "day": 3, "operations": [TIME, {
            "action": "time", "day": 3, "slot": 3, "start": "16:00", "end": "17:00"}]}),
    "cd_time": (CHENGDU, CHENGDU_ID, None),
}


def case_details(key):
    folder, candidate, operation = CASES[key]
    if operation is None:
        archive = json.loads((folder / "CANDIDATE_ARCHIVE.json").read_text(encoding="utf-8"))["archive"]
        plan = next(entry["itinerary"] for entry in archive if entry["id"] == candidate)
        day = plan["itinerary"][0]
        slot, item = next((i, item) for i, item in enumerate(day["activities"], 1) if item["type"] == "attraction")
        hour, minute = map(int, item["end_time"].split(":"))
        hour, minute = divmod(60 * hour + minute - 15, 60)
        operation = {"action": "time", "day": day["day"], "slot": slot,
                     "start": item["start_time"], "end": f"{hour:02}:{minute:02}"}
    return folder, candidate, operation


def worker(out, cases, deep=False):
    start = perf_counter()
    from chinatravel.personalization.cli import DATA, environment
    from chinatravel.personalization.world_facts import WorldFacts
    from chinatravel.personalization import integrated_planning
    imports = perf_counter() - start
    tracked = {}
    detailed = {}
    read_paths = Counter()

    def profile(frame, event, _arg):
        if event == "call":
            name = frame.f_code.co_name
            path = frame.f_code.co_filename.replace("\\", "/")
            module = path.rsplit("/", 1)[-1]
            label = None
            if name in ("verify", "verify_profile", "configuration") and "/environment/sandbox/" in path:
                label = module + "." + name
            elif name == "__init__" and "/environment/" in path:
                cls = frame.f_locals.get("self")
                label = type(cls).__name__ + ".init" if cls is not None else None
            elif name == "read_json" and "/pandas/io/json/" in path:
                label = "pandas.read_json"
                filename = frame.f_locals.get("path_or_buf")
                if isinstance(filename, (str, Path)):
                    read_paths[str(filename).replace("\\", "/").rsplit("/intercity_transport/", 1)[-1]] += 1
            if label:
                tracked[frame] = (label, perf_counter())
        elif event == "return" and frame in tracked:
            label, begin = tracked.pop(frame)
            count, duration = detailed.get(label, (0, 0))
            detailed[label] = (count + 1, duration + perf_counter() - begin)

    if deep:
        sys.setprofile(profile)
    setup_start = perf_counter()
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    setup = perf_counter() - setup_start
    start = perf_counter()
    from chinatravel.personalization import itinerary_edit
    imports += perf_counter() - start
    spans = {}

    def wrap(function, label):
        def timed(*args, **kwargs):
            begin = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                spans.setdefault(label, []).append(perf_counter() - begin)
        return timed

    for name in ("prepare_snapshot_routes", "_batch_connections", "evaluate_archive", "_card"):
        setattr(itinerary_edit, name, wrap(getattr(itinerary_edit, name), name))
    rows = []
    for index, name in enumerate(cases):
        spans.clear()
        source, candidate, operation = case_details(name)
        begin = perf_counter()
        result = itinerary_edit.edit_preview(source, out / f"{index}_{name}", candidate, operation, env, facts)
        change = result.get("edit", {})
        rows.append({"case":name, "state":result["state"], "message":result["message"],
                     "edit_seconds":round(perf_counter() - begin, 4),
                     "spans":{key:{"seconds":round(sum(times), 4), "calls":len(times)} for key,times in spans.items()},
                     "change":change})
    if deep:
        sys.setprofile(None)
    return {"imports_seconds":round(imports, 4), "setup_seconds":round(setup, 4), "rows":rows,
            "details":{key:{"calls":count,"seconds":round(duration, 4)}
                       for key,(count,duration) in detailed.items()},
            "json_files_read":len(read_paths), "json_reads":sum(read_paths.values()),
            "repeated_json_reads":sum(count - 1 for count in read_paths.values()),
            "most_read_jsons":read_paths.most_common(5)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    if args.worker:
        result = worker(args.out, args.cases, args.deep)
    else:
        runs = []
        arms = [(key, [key]) for key in args.cases] + [("reused_environment", args.cases + args.cases)]
        for name, cases in arms:
            started = perf_counter()
            folder = args.out / name
            argv = [sys.executable, "-B", "-X", "utf8", __file__, "--worker", "--out", str(folder), "--cases", *cases]
            subprocess.run(argv + (["--deep"] if args.deep else []), cwd=ROOT, check=True, timeout=100)
            row = json.loads((folder / "PROFILE.json").read_text(encoding="utf-8"))
            row.update(arm=name, wall_seconds=round(perf_counter() - started, 3))
            runs.append(row)
            print(name, row["wall_seconds"], [item["state"] for item in row["rows"]], flush=True)
        result = {"runs":runs}
    (args.out / "PROFILE.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
