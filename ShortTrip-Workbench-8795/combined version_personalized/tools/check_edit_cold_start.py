"""Compare cold edit processes using the same archived requests and actions."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

from profile_edit_latency import ROOT, case_details
from chinatravel.personalization.cli import DATA


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_process(out, cases):
    tasks = []
    for i, name in enumerate(cases):
        source, candidate, operation = case_details(name)
        folder = out / f"{i}_{name}"
        folder.mkdir(parents=True)
        (folder / "operation.json").write_text(json.dumps(operation, ensure_ascii=False), encoding="utf-8")
        tasks.append({"folder":str(folder.resolve()), "source":str(source), "candidate":candidate})
    args = [sys.executable, "-B", "-X", "utf8", "-m", "chinatravel.personalization.edit_worker", "--data", str(DATA)]
    begin = perf_counter()
    process = subprocess.run(args, input="".join(json.dumps(task) + "\n" for task in tasks),
                             encoding="utf-8", capture_output=True, timeout=100, cwd=ROOT)
    wall = perf_counter() - begin
    (out / "worker.log").write_text(process.stdout + process.stderr, encoding="utf-8")
    process.check_returncode()
    rows = []
    for name, task in zip(cases, tasks, strict=True):
        folder = Path(task["folder"])
        status = read(folder / "run/STATUS.json")
        output = folder / "run/PLAN_VIEW.json"
        rows.append({"case":name, "state": status["state"], "message":status["message"],
                     "before":status.get("edit", {}).get("before"), "after":status.get("edit", {}).get("after"),
                     "candidate":read(output)["recommended_id"] if output.is_file() else None,
                     "timing":read(folder / "WORKER_TIMING.json")})
    return {"process_seconds":round(wall, 4), "rows":rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    output = []
    manifest = [("shanghai", ["sh_lighter"]), ("chengdu", ["cd_time"]),
                ("mixed", ["sh_time", "cd_time", "sh_locked", "sh_lighter"])]
    for section, cases in manifest:
        row = run_process(args.out / section, cases)
        row["test"] = section
        output.append(row)
        print(section, row["process_seconds"], [item["state"] for item in row["rows"]], flush=True)
    (args.out / "COLD.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
