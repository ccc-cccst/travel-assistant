"""Compare a dated AMap subway lookup with three verified, archived return transfers."""
import argparse
from datetime import date, datetime, timedelta
import json
from math import ceil
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

PLANNER = Path(__file__).resolve().parents[1]
ROOT = PLANNER.parent
WEB = ROOT / "shorttrip-webui/shorttrip-webui/webui"
sys.path[:0] = [str(WEB), str(WEB / "backend")]
from amap_route import RouteService, find_key
from personalized_archive import load_archive


CASES = (
    ("0241a805a917423ca6302303cdabc5fc", "上海"),
    ("33536e6cf4964a6ba8f78d6e2c035c4a", "南京"),
    ("2a67a92984d3488b8bae2d15a4512659", "杭州"),
)


def minutes(clock):
    h, m = map(int, clock.split(":"))
    return h * 60 + m


def compare(service, job, city, trial_date):
    group = load_archive(PLANNER / "result/manual" / job, ROOT / "ShortTrip/artifacts/data")
    version = next(v for v in group["versions"] if v["id"] == group["recommendedId"])
    final_day = version["plan"]["itinerary"][-1]
    move = next(a for a in final_day["activities"] if a.get("_lock") == "departure")
    legs = move["transports"]
    first, last = legs[0], legs[-1]
    assert first["_from"] and last["_to"] and any(s["mode"] == "metro" for s in legs)
    leave = minutes(first["start_time"])
    frame = minutes(move["start_time"]) - leave
    reserve = {"train": 60, "airplane": 120}[move["type"]]
    original = minutes(last["end_time"]) - leave
    requested = f"{trial_date}T{first['start_time']}"
    t0 = perf_counter()
    route = service.route(first["_from"], last["_to"], "subway", city, departure=requested)
    row = dict(city=city, job=job, candidate=version["id"], day=final_day["day"],
               origin=first["start"], destination=last["end"], requested_departure=requested,
               archived_start_date=version["startDate"], return_time=move["start_time"],
               reserve_minutes=reserve, baseline_minutes=original, baseline_spare=frame-reserve-original,
               elapsed_seconds=round(perf_counter()-t0, 3), source=route["source"])
    seconds = route.get("durationSeconds")
    if route["source"] != "amap" or route.get("unavailable") or not seconds:
        return {**row, "status": "unverified", "reason": route.get("reason") or "no_timed_subway_option"}
    duration = ceil(seconds / 60)
    arrival = datetime.fromisoformat(requested) + timedelta(minutes=duration)
    return {**row, "status": "received", "queried_at": route["retrievedAt"], "estimated_minutes": duration,
            "arrival": arrival.strftime("%H:%M"), "delta_minutes": duration-original,
            "spare_after_reserve": frame-reserve-duration, "until_return_minutes": frame-duration}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="test date of the return leg")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("choose a new run file")
    with TemporaryDirectory() as temp:
        service = RouteService(find_key(), Path(temp))
        rows = [compare(service, job, city, args.date) for job, city in CASES]
    report = {"started_for_date": args.date.isoformat(), "mode": "subway", "live_calls": service.calls,
              "cases": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
