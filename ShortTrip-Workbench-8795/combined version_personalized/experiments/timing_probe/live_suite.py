"""Run paired live profiles with identical local model and sandbox inputs."""

import argparse
import ctypes
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler
from zipfile import ZipFile


PROJECT = Path(__file__).resolve().parents[2]
UPSTREAM = PROJECT.parent / "ChinaTravel"
RUNTIME = PROJECT / "artifacts/local"
MODEL = RUNTIME / "Qwen3.5-4B-Q8_0.gguf"
SERVER = RUNTIME / "llama-b12211-cuda12.4/llama-server.exe"


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def snapshot(source, target, working_tree=False):
    revision = git(source, "rev-parse", "HEAD").decode().strip()
    with ZipFile(BytesIO(git(source, "archive", "--format=zip", revision))) as archive:
        archive.extractall(target)
    changes = {}
    if working_tree:
        names = git(source, "diff", "--name-only", "-z", "HEAD").decode().split("\0")
        for name in filter(None, names):
            current, copied = source / name, target / name
            if current.is_file():
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, copied)
                changes[name] = digest(copied)
            elif copied.is_file():
                copied.unlink()
                changes[name] = None
    return {"revision": revision, "working_tree_files": changes}


def healthy(opener, endpoint):
    try:
        with opener.open(endpoint + "/health", timeout=1) as response:
            return json.load(response).get("status") == "ok"
    except OSError:
        return False


def measure(args, arm, case, source, query):
    case_dir = args.out / case / arm
    case_dir.mkdir(parents=True, exist_ok=False)
    endpoint = f"http://127.0.0.1:{args.port}"
    opener = build_opener(ProxyHandler({}))
    if healthy(opener, endpoint):
        raise RuntimeError(f"Port {args.port} already serves a model")
    runtime = getattr(args, "runtime", RUNTIME)
    server_path = runtime / SERVER.relative_to(RUNTIME)
    model_path = runtime / MODEL.relative_to(RUNTIME)
    server_args = [str(server_path), "--model", str(model_path), "--alias", "qwen3.5-4b-q8",
                   "--host", "127.0.0.1", "--port", str(args.port), "--ctx-size", "16384",
                   "--parallel", "1", "--device", "CUDA0", "--n-gpu-layers", "99",
                   "--flash-attn", "on", "--jinja", "--reasoning-budget", "0"]
    server_env = {**os.environ, "LLAMA_ARG_CHAT_TEMPLATE_KWARGS": '{"enable_thinking":false}'}
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with (case_dir / "server.log").open("wb") as server_log:
        server = subprocess.Popen(server_args, cwd=source, env=server_env,
                                  stdout=server_log, stderr=subprocess.STDOUT,
                                  creationflags=no_window)
        try:
            ready_at = time.monotonic() + 45
            while not healthy(opener, endpoint):
                if server.poll() is not None or time.monotonic() >= ready_at:
                    raise RuntimeError(f"Qwen startup failed. See {case_dir / 'server.log'}")
                time.sleep(.4)
            profile_script = getattr(args, "profile_script", Path(__file__).with_name("live_profile.py"))
            command = [sys.executable, "-B", "-X", "utf8",
                       str(profile_script), "--arm", arm,
                       "--source", str(source), "--query", str(query), "--out", str(case_dir / "profile"),
                       "--seconds", str(args.seconds), "--max-tokens", str(args.max_tokens),
                       "--endpoint", endpoint + "/v1"]
            if getattr(args, "original_stops", False):
                command.append("--original-stops")
            if getattr(args, "concise_ranking", False):
                command.append("--concise-ranking")
            if getattr(args, "preflight", False):
                command.append("--preflight")
            if getattr(args, "return_batch_size", 0):
                command.extend(("--return-batch-size", str(args.return_batch_size)))
            if getattr(args, "adaptive_rescue", False):
                command.append("--adaptive-rescue")
            print(f"{case} {arm}: started", flush=True)
            began = time.perf_counter()
            with (case_dir / "worker.log").open("wb") as log:
                worker = subprocess.Popen(command, cwd=source, stdout=log,
                                          stderr=subprocess.STDOUT, creationflags=no_window)
                try:
                    code = worker.wait(timeout=args.seconds + 20)
                except subprocess.TimeoutExpired:
                    subprocess.run(["taskkill", "/PID", str(worker.pid), "/T", "/F"],
                                   check=True, stdout=log, stderr=subprocess.STDOUT, timeout=15)
                    worker.wait(timeout=15)
                    code = "external_guard"
            result = {"case": case, "arm": arm, "worker_exit": code,
                      "worker_seconds": time.perf_counter() - began}
            (case_dir / "supervisor.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"{case} {arm}: {code}, {result['worker_seconds']:.1f}s", flush=True)
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--cases", nargs="+", default=["easy_1", "easy_3"])
    parser.add_argument("--query-dir", type=Path, default=PROJECT / "experiments/c_only/queries")
    parser.add_argument("--reference", type=Path, help="Reuse the source and data snapshots of an existing batch")
    parser.add_argument("--combine", action="store_true", help="Generate a report including the reference batch")
    parser.add_argument("--seconds", type=float, default=360)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--port", type=int, default=18084)
    parser.add_argument("--original-stops", action="store_true")
    args = parser.parse_args()
    if min(args.seconds, args.max_tokens) <= 0 or len(set(args.cases)) != len(args.cases):
        parser.error("Use positive budgets and unique cases")
    if args.combine and not args.reference:
        parser.error("--combine needs --reference")
    args.out.mkdir(parents=True, exist_ok=False)
    sources = {arm: args.out / "sources" / arm for arm in ("shorttrip", "native")}
    if args.reference:
        reference = json.loads((args.reference / "manifest.json").read_text(encoding="utf-8"))
        identities = reference["sources"]
        for arm, target in sources.items():
            shutil.copytree(args.reference / "sources" / arm, target)
    else:
        identities = {
            "shorttrip": snapshot(PROJECT, sources["shorttrip"], working_tree=True),
            "native": snapshot(UPSTREAM, sources["native"]),
        }
        database = PROJECT / "chinatravel/environment"
        for source in sources.values():
            for language in ("database", "database_en"):
                shutil.copytree(database / language, source / "chinatravel/environment" / language)
    database = sources["shorttrip"] / "chinatravel/environment"
    data_sha256 = {str(path.relative_to(database)): digest(path)
                   for language in ("database", "database_en")
                   for path in (database / language).rglob("*") if path.is_file()}
    native_db = sources["native"] / "chinatravel/environment"
    if any(digest(native_db / relative) != expected for relative, expected in data_sha256.items()):
        raise ValueError("The two sandboxes differ")
    if args.reference and data_sha256 != reference["data_sha256"]:
        raise ValueError("The reference sandbox changed")
    inputs = args.out / "queries"
    inputs.mkdir()
    for case in args.cases:
        if not case.isidentifier():
            parser.error(f"Invalid case {case!r}")
        shutil.copy2(args.query_dir / f"{case}.json", inputs)
    manifest = {"sources": identities, "cases": args.cases, "seconds": args.seconds,
                "max_tokens": args.max_tokens, "seed": 20261112, "temperature": 0,
                "stop_mode": "original" if args.original_stops else "matched_cutoff",
                "backend": "CUDA0; fresh Qwen server before each arm", "python": sys.version,
                "model_manifest": json.loads((RUNTIME / "runtime_manifest.json").read_text(encoding="utf-8"))["files"][0],
                "data_sha256": data_sha256,
                "query_sha256": {case: digest(inputs / f"{case}.json") for case in args.cases},
                "harness_sha256": {name: digest(Path(__file__).with_name(name)) for name in (
                    "spans.py", "live_profile.py", "live_suite.py")}}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {"state": "running", "current": None, "finished": [], "failures": []}

    def checkpoint():
        temporary = args.out / "STATUS.tmp"
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.out / "STATUS.json")

    power = ctypes.windll.kernel32
    power.SetThreadExecutionState(0x80000001)
    try:
        for i, case in enumerate(args.cases):
            order = ("shorttrip", "native") if i % 2 == 0 else ("native", "shorttrip")
            for arm in order:
                task = {"case": case, "arm": arm}
                status["current"] = task
                checkpoint()
                try:
                    measure(args, arm, case, sources[arm], inputs / f"{case}.json")
                    if not (args.out / case / arm / "profile/run.json").is_file():
                        raise RuntimeError("Missing worker timing record")
                except Exception as error:
                    status["failures"].append({**task, "error": f"{type(error).__name__}: {error}"})
                    print(f"{case} {arm}: {error}", flush=True)
                else:
                    status["finished"].append(task)
                checkpoint()
        status["state"] = "reporting"
        status["current"] = None
        checkpoint()
        report = args.out / "analysis"
        command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("live_report.py"))]
        if args.combine:
            command.extend([str(args.reference), "--also", str(args.out)])
        else:
            command.append(str(args.out))
        subprocess.run([*command, "--out", str(report)], cwd=PROJECT, check=True)
        status["report"] = str(report / "REPORT.md")
        status["state"] = "completed_with_errors" if status["failures"] else "completed"
        checkpoint()
    except BaseException as error:
        status["state"] = "interrupted"
        status["error"] = f"{type(error).__name__}: {error}"
        checkpoint()
        raise
    finally:
        power.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
