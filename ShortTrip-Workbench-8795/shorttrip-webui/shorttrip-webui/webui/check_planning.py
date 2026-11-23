"""Run the frozen P036 requests through the ShortTrip workbench."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener
from uuid import uuid4


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def call(base, path, body=None, query=None, timeout=42):
    url = base + "/api/personalized/" + path
    if query:
        url += "?" + urlencode(query)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        with error:
            msg = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {error.code} {path}: {msg[:500]}") from error


def check(base, case, folder, start_date=None, provider=None):
    folder.mkdir()
    started = time.monotonic()
    row = {"case": case["case"], "level": case["level"]}
    step = "interpret"
    try:
        form = {"start_date": start_date} if start_date else {}
        payload = {"raw_text": case["raw_text"], "form": form}
        if provider:
            payload["model_provider"] = provider
        spec = call(base, "interpret", payload)
        save(folder / "SPEC.json", spec)
        row["interpret_s"] = round(time.monotonic() - started, 2)
        row["request"] = spec["request_id"]
        row["model_interpretation"] = spec.get("model")
        row["issues"] = [{"field": i.get("field"), "code": i.get("code"), "message": i.get("message")}
                         for i in spec["issues"]]
        row["core"] = {field: [i["value"] for i in spec["items"] if i["field"] == field]
                       for field in ("origin_city", "destination_city", "people", "days")}
        row["core_differences"] = {key: value for key, value in case["corpus_core"].items()
                                   if value not in row["core"].get(key, [])}
        if not spec["ready"]:
            row["outcome"] = "needs_confirmation"
            return row
        step = "start"
        submitted = time.monotonic()
        job = call(base, "start", {"request_id": spec["request_id"],
                                  "request_revision": spec["request_revision"], "submission_id": uuid4().hex})
        row["job"] = job["id"]
        row["url"] = base + "/?" + urlencode({"job": job["id"], "request": spec["request_id"]})
        step = "job"
        errors = []
        while time.monotonic() - submitted < 140:
            try:
                status = call(base, "job", query={"id": job["id"]}, timeout=10)
            except (OSError, URLError, RuntimeError) as error:
                errors.append(str(error))
                row["poll_errors"] = errors
                if len(errors) >= 4:
                    raise
                time.sleep(.8)
                continue
            elapsed = round(time.monotonic() - submitted, 2)
            group = status.get("group") or {}
            versions = group.get("versions", [])
            if versions and "first_s" not in row:
                row["first_s"] = elapsed
                row["first_candidate"] = group.get("firstId") or group.get("recommendedId")
            if status["state"] not in ("starting", "queued", "planning"):
                break
            time.sleep(.85)
        else:
            raise TimeoutError("回归客户端等待超过 140 秒")
        save(folder / "RESULT.json", status)
        row.update({"final_s": elapsed, "planner_state": status["state"],
                    "reason": status.get("message"), "model_planning": status.get("model")})
        previews = status.get("previews") or []
        recovery = status.get("recovery") or {}
        row["outcome"] = ("complete" if status["state"] == "complete" and versions else
                          "conditional" if previews else "city_draft" if recovery.get("draft") else "undelivered")
        row["candidate_count"] = status.get("candidate_count", 0)
        row["previews"] = len(previews)
        row["alternatives"] = sum(bool(v["archive"].get("alternative")) for v in versions)
        row["verification_failures_shown"] = [
            v["id"] for v in versions if v["archive"]["validation"].get("passed") is not True]
        recommended = next((v for v in versions if v["id"] == group.get("recommendedId")), None)
        if recommended:
            row["candidate"] = recommended["id"]
            row["pending"] = recommended["archive"]["pending"]
            row["plan_issues"] = recommended["archive"]["validation"].get("issues", [])
            row["plan_summary"] = {key: recommended["archive"]["metrics"].get(key)
                                   for key in ("total_cost_cny", "visit_minutes", "city_transit_minutes")}
        return row
    except Exception as error:
        row.update({"outcome": "gateway_error", "step": step, "reason": str(error)})
        return row
    finally:
        row["elapsed_s"] = round(time.monotonic() - started, 2)
        save(folder / "row.json", row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8790")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--start-date", help="Common date supplied as a form field for this controlled run")
    parser.add_argument("--provider", choices=("deepseek", "qwen"))
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8-sig"))["cases"]
    config = call(args.url, "options")
    if not (config.get("model_providers", {}).get(args.provider) if args.provider else config.get("model_ready")):
        raise SystemExit("规划器尚无模型配置，原话回归无法启动。")
    args.output.mkdir(parents=True)
    save(args.output / "RUN.json", {"manifest": str(args.manifest.resolve()), "base": args.url,
                                    "workers": args.workers, "case_count": len(cases), "start_date": args.start_date,
                                    "provider": args.provider})
    rows = {}
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(check, args.url, case, args.output / case["case"], args.start_date, args.provider)
                   for case in cases]
        for future in as_completed(futures):
            row = future.result()
            rows[row["case"]] = row
            save(args.output / "SUMMARY.json", [rows[c["case"]] for c in cases if c["case"] in rows])
            print(f"{len(rows)}/{len(cases)} {row['case']} {row['outcome']} {row['elapsed_s']}s", flush=True)


if __name__ == "__main__":
    main()
