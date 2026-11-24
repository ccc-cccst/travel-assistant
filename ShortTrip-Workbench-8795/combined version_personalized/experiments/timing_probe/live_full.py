"""Prepare and resume a full local ChinaTravel timing comparison."""

import argparse
from contextlib import contextmanager
import csv
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from itertools import zip_longest
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from live_suite import MODEL, PROJECT, RUNTIME, digest, measure


TIERS = ("easy", "medium", "human")
ARMS = ("shorttrip", "native")
HARNESS = ("live_full.py", "live_suite.py", "live_report.py", "live_profile.py", "spans.py")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for _ in range(20):
        try:
            pending.replace(path)
            return
        except PermissionError:
            time.sleep(.05)
    pending.replace(path)


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def prepare(out, reference):
    sys.path.insert(0, str(PROJECT))
    from reproduction.prepare import normalize

    prior = read(reference / "manifest.json")
    out.mkdir(parents=True, exist_ok=False)
    sources = out / "sources"
    shutil.copytree(reference / "sources", sources)
    frozen_project = sources / "shorttrip"
    frozen_harness = frozen_project / "experiments/timing_probe"
    frozen_harness.mkdir()
    for name in HARNESS:
        shutil.copy2(Path(__file__).with_name(name), frozen_harness / name)
    evaluator = frozen_project / "experiments/evaluation"
    evaluator.mkdir()
    shutil.copy2(PROJECT / "experiments/evaluation/validation.py", evaluator / "validation.py")

    for arm in ARMS:
        database = sources / arm / "chinatravel/environment"
        if any(digest(database / p) != expected for p, expected in prior["data_sha256"].items()):
            raise ValueError(f"Reference sandbox has changed for {arm}")
    for name in ("live_profile.py", "spans.py"):
        if digest(frozen_harness / name) != prior["harness_sha256"][name]:
            raise ValueError(f"The timing protocol differs from the pilot in {name}")
    if digest(MODEL) != prior["model_manifest"]["sha256"]:
        raise ValueError("The local Qwen weights differ from the pilot")
    model_stat = MODEL.stat()

    queries = out / "queries"
    queries.mkdir()
    groups, input_hashes, uids = {}, {}, set()
    for tier in TIERS:
        dataset = PROJECT / "reproduction/data" / f"{tier}.csv"
        input_hashes[tier] = digest(dataset)
        groups[tier] = []
        with dataset.open(encoding="utf-8-sig", newline="") as file:
            for index, row in enumerate(csv.DictReader(file), 1):
                query = normalize(row)
                uid = query["uid"]
                if uid in uids:
                    raise ValueError(f"Duplicate dataset UID {uid}")
                uids.add(uid)
                case = f"{tier}_{index:03d}"
                groups[tier].append(case)
                write(queries / f"{case}.json", query)
    cases = [case for round_cases in zip_longest(*(groups[tier] for tier in TIERS))
             for case in round_cases if case is not None]
    manifest = {**prior, "scope": "full_local_datasets", "cases": cases, "tiers": groups,
                "reference": str(reference), "runtime": str(RUNTIME), "port": 18084,
                "model_file": {"size": model_stat.st_size, "mtime_ns": model_stat.st_mtime_ns},
                "input_csv_sha256": input_hashes,
                "query_sha256": {case: digest(queries / f"{case}.json") for case in cases},
                "harness_sha256": {name: digest(frozen_harness / name) for name in HARNESS},
                "validator_sha256": digest(evaluator / "validation.py")}
    write(out / "manifest.json", manifest)
    write(out / "STATUS.json", {"state": "prepared", "current": None, "finished": 0,
                                "total": len(cases) * 2, "failures": [], "planner_errors": 0,
                                "tiers": {tier: {"finished": 0, "total": 2 * len(groups[tier])}
                                          for tier in TIERS}})
    print("Prepared", {tier: len(groups[tier]) for tier in TIERS}, "total runs", len(cases) * 2)


@contextmanager
def lock(out):
    with (out / "batch.lock").open("a+b") as file:
        if file.tell() == 0:
            file.write(b"1")
            file.flush()
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("A full batch process is already running") from error
        try:
            yield
        finally:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)


def existing(out, case, arm):
    path = out / case / arm / "profile/run.json"
    if path.is_file():
        try:
            return read(path)
        except (OSError, json.JSONDecodeError):
            pass
    return None


def archive_incomplete(out, case, arm):
    old = out / case / arm
    if old.exists():
        archived = out / "interrupted" / f"{case}_{arm}_{timestamp()}"
        if not old.resolve().is_relative_to(out) or not archived.resolve().is_relative_to(out):
            raise ValueError("The run directory escaped the experiment root")
        archived.parent.mkdir(exist_ok=True)
        old.rename(archived)


def execute(out):
    manifest = read(out / "manifest.json")
    tasks = [(case, arm) for i, case in enumerate(manifest["cases"])
             for arm in (ARMS if i % 2 == 0 else ARMS[::-1])]
    completed = {(case, arm): row for case, arm in tasks if (row := existing(out, case, arm))}
    prior = read(out / "STATUS.json")
    if len(completed) == len(tasks) and prior["state"] == "completed" and Path(prior["report"]).is_file():
        print(f"All runs are complete. Report: {prior['report']}", flush=True)
        return
    options = argparse.Namespace(out=out, seconds=manifest["seconds"], max_tokens=manifest["max_tokens"],
                                 port=manifest["port"], runtime=Path(manifest["runtime"]))
    model = options.runtime / MODEL.relative_to(RUNTIME)
    model_identity = model.stat()
    if (model_identity.st_size, model_identity.st_mtime_ns) != (
            manifest["model_file"]["size"], manifest["model_file"]["mtime_ns"]):
        if digest(model) != manifest["model_manifest"]["sha256"]:
            raise ValueError("Qwen weights changed since batch preparation")
    status = {"state": "running", "pid": os.getpid(), "current": None, "total": len(tasks),
              "failures": [], "latest": None}

    def checkpoint():
        status["finished"] = len(completed)
        status["planner_errors"] = sum(row["stop_reason"] == "error" for row in completed.values())
        counts = {tier: 0 for tier in TIERS}
        for case, _ in completed:
            counts[case.split("_", 1)[0]] += 1
        status["tiers"] = {tier: {"finished": counts[tier], "total": 2 * len(manifest["tiers"][tier])}
                           for tier in TIERS}
        write(out / "STATUS.json", status)

    power = ctypes.windll.kernel32
    power.SetThreadExecutionState(0x80000001)
    checkpoint()
    consecutive_failures = 0
    try:
        for case, arm in tasks:
            if (case, arm) in completed:
                continue
            stat = model.stat()
            if (stat.st_size, stat.st_mtime_ns) != (model_identity.st_size, model_identity.st_mtime_ns):
                raise ValueError("Qwen weights changed while the batch was running")
            query = out / "queries" / f"{case}.json"
            if digest(query) != manifest["query_sha256"][case]:
                raise ValueError(f"Input changed for {case}")
            status["current"] = {"case": case, "arm": arm, "uid": read(query)["uid"]}
            checkpoint()
            try:
                archive_incomplete(out, case, arm)
                measure(options, arm, case, out / "sources" / arm, query)
                row = existing(out, case, arm)
                if row is None:
                    raise RuntimeError("No complete timing record")
            except Exception as error:
                failure = {**status["current"], "error": f"{type(error).__name__}: {error}"}
                status["failures"].append(failure)
                print(failure, flush=True)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise RuntimeError("Three consecutive runs failed to write timing records") from error
            else:
                completed[case, arm] = row
                status["latest"] = {**status["current"], "stop_reason": row["stop_reason"],
                                    "wall_seconds": row["wall_seconds"],
                                    "candidate_count": row["candidate_count"]}
                consecutive_failures = 0
            checkpoint()

        status.update(state="reporting", current=None)
        checkpoint()
        analysis = out / "analysis"
        if analysis.exists():
            analysis = out / ("analysis_" + timestamp())
        subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("live_report.py")),
                        str(out), "--out", str(analysis)], cwd=PROJECT, check=True)
        status["state"] = "completed" if len(completed) == len(tasks) else "completed_with_missing_runs"
        status["report"] = str(analysis / "REPORT.md")
        checkpoint()
    except BaseException as error:
        status["state"] = "interrupted"
        status["error"] = f"{type(error).__name__}: {error}"
        checkpoint()
        raise
    finally:
        power.SetThreadExecutionState(0x80000000)


def launch(out):
    # Leave the command's Windows job when running from an automation session.
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    in_job = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise ctypes.WinError()
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    if in_job.value:
        flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
    script = out / "sources/shorttrip/experiments/timing_probe/live_full.py"
    command = [sys.executable, "-B", "-X", "utf8", str(script), "run", "--out", str(out)]
    # A duplicate launch is rejected by the worker's file lock.
    with (out / "background.log").open("ab") as log:
        process = subprocess.Popen(command, cwd=out, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    write(out / f"launch_{timestamp()}.json", {"pid": process.pid, "command": command})
    print(f"Background launcher PID {process.pid}. Progress: {out / 'STATUS.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "launch", "run"))
    parser.add_argument("--out", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--reference", type=lambda p: Path(p).resolve())
    args = parser.parse_args()
    if args.command == "prepare":
        if args.reference is None:
            parser.error("prepare needs --reference")
        prepare(args.out, args.reference)
    elif args.command == "launch":
        launch(args.out)
    else:
        with lock(args.out):
            execute(args.out)


if __name__ == "__main__":
    main()
