"""Measure fixture previews through 8795 and write repeatable decisions for comparison."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from time import perf_counter, sleep
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from profile_edit_latency import case_details

ROOT = "http://127.0.0.1:8795/api/itinerary-edits/"
SH = {"job": "01b135fda6c446b38440ab8716b0f4c2", "candidate": "943d6f07725c3c87313e8f57",
      "parent": "55350b07bb5546b1a8fbe374c6af0bc5"}
CD = {"job": "7cfccaa570c744818c257c37a4256826", "candidate": "6e08e84d0129a23b651bd0bc"}


def send(operation, payload=None, **query):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    url = ROOT + operation + ("?" + urlencode(query) if query else "")
    request = Request(url, body, {"Content-Type":"application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=60) as response:
        return response.status, json.load(response)


def preview(name):
    _, candidate, operation = case_details(name)
    base = CD if name.startswith("cd_") else SH

    def edit(op):
        fields = {key:value for key,value in op.items() if key not in ("day","slot","operations")}
        if "slot" in op:
            fields["activity"] = f"{candidate}-d{op['day']}-a{op['slot']}"
        else:
            fields["day"] = op["day"]
        if op["action"] == "batch":
            fields.update(schema_version=1, operations=[edit(item) for item in op["operations"]])
        return fields

    begin = perf_counter()
    code, initial = send("preview", {**base, **edit(operation)})
    assert code == 202, initial
    while perf_counter() - begin < 65:
        _, result = send("state", id=initial["id"])
        if result["state"] not in ("starting","planning"):
            break
        sleep(.12)
    else:
        raise RuntimeError(f"{name}: no preview response")
    wall = round(perf_counter() - begin, 3)
    assert result["state"] == ("edit_conflict" if name.endswith("locked") else "complete"), result
    row = {"case":name, "seconds":wall, "id":initial["id"], "state":result["state"], "message":result["message"]}
    if result["state"] == "complete":
        plan = result["group"]["versions"][0]
        assert plan["archive"]["validation"]["passed"] and not plan["revision"]["adopted"]
        row.update(candidate=plan["id"], metrics=plan["archive"]["metrics"],
                   locks=[(day["day"], item["_lock"], item["start_time"])
                          for day in plan["plan"]["itinerary"] for item in day["activities"] if item.get("_lock")])
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrent", action="store_true")
    options = parser.parse_args()
    rows = []
    for name in ("sh_time", "sh_lighter", "sh_locked", "cd_time", "sh_lighter", "cd_time"):
        row = preview(name)
        rows.append(row)
        print(row["case"], row["seconds"], row["state"], flush=True)
    if options.concurrent:
        with ThreadPoolExecutor(2) as pool:
            pairs = list(pool.map(preview, ("sh_lighter", "cd_time")))
        for row in pairs:
            row["concurrent"] = True
            rows.append(row)
            print(row["case"], row["seconds"], row["state"], "concurrent", flush=True)
    options.out.write_text(json.dumps({"rows":rows},ensure_ascii=False,indent=2),encoding="utf-8")
