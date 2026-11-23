"""Run the frozen P036 corpus through the same 8795 backend used for P058."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from acceptance_p036 import OUT, ROOT, read, save
from report_workbench import report


BASE = "http://127.0.0.1:8795/api/personalized/"


def call(endpoint, body=None, **query):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    url = BASE + endpoint + ("?" + urlencode(query) if query else "")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=60) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def run_case(case, out, date):
    folder = out / case["case"]
    started = monotonic()
    code, spec = call("interpret", {"raw_text": case["raw_text"], "form": {"start_date": date},
                                   "model_provider": "deepseek"})
    save(folder / "SPEC.json", spec)
    row = {"case": case["case"], "level": case["level"], "interpret_s": round(monotonic() - started, 2),
           "alternatives": 0, "interpretation_code": code, "model_interpretation": spec.get("model"),
           "issues": [item["code"] for item in spec.get("issues", [])]}
    ready = bool(spec.get("ready"))
    if code != 200:
        return {**row, "outcome": "interpretation_error", "reason": spec.get("message")}
    if not ready:
        return {**row, "outcome": "needs_confirmation", "reason": spec.get("state")}

    code, launch = call("start", {key: spec[key] for key in ("request_id", "request_revision")} |
                        {"submission_id": uuid4().hex})
    if code != 202:
        return {**row, "outcome": "start_error", "reason": launch.get("message")}
    job = launch["id"]
    row["job"] = job
    until = monotonic() + 140
    while monotonic() < until:
        code, result = call("job", id=job)
        if code != 200:
            row.setdefault("poll_errors", []).append(code)
            sleep(.65)
            continue
        if (result.get("group") or result.get("previews")) and row.get("first_s") is None:
            row["first_s"] = round(monotonic() - started, 2)
        if result["state"] not in ("starting", "planning"):
            break
        sleep(.65)
    else:
        return {**row, "outcome": "timeout", "planner_state": "client_timeout"}

    save(folder / "RESULT.json", result)
    row.update(planner_state=result["state"], reason=result.get("message"), final_s=round(monotonic()-started, 2),
               model_planning=result.get("model"))
    if result["state"] == "complete" and (group := result.get("group")):
        chosen = next(v for v in group["versions"] if v["id"] == group["recommendedId"])
        archives = [v["archive"] for v in group["versions"]]
        row.update(outcome="complete", candidate=chosen["id"],
                   alternatives=sum(bool(a.get("alternative")) for a in archives),
                   verification_failures_shown=[a["candidateId"] for a in archives if not a["validation"]["passed"]],
                   pending=chosen["archive"]["pending"], metrics=chosen["archive"]["metrics"],
                   goals=chosen["archive"]["goals"])
    elif result.get("previews"):
        row["outcome"] = "conditional"
    elif result.get("recovery", {}).get("draft"):
        row["outcome"] = "city_draft"
    else:
        row["outcome"] = result["state"]
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--date", default="2026-11-22")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    cases = read(OUT / "FROZEN30.json")["cases"]
    assert len(cases) == 30
    rows = []
    with ThreadPoolExecutor(3) as pool:
        futures = {pool.submit(run_case, case, args.out, args.date): case for case in cases}
        for future in as_completed(futures):
            case = futures[future]
            try:
                record = future.result()
            except (OSError, ValueError, KeyError) as error:
                record = {"case": case["case"], "level": case["level"], "outcome": "runner_error",
                          "reason": str(error), "alternatives": 0}
            rows.append(record)
            save(args.out / case["case"] / "row.json", record)
            save(args.out / "SUMMARY.json", sorted(rows, key=lambda row: row["case"]))
            print(f"{len(rows)}/30 {record['case']} {record['outcome']}", flush=True)
    report(args.out)
