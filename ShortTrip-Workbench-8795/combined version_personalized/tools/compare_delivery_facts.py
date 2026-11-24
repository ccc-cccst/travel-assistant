"""Replay one frozen published plan per case, with and without post-publication online checks."""
import argparse
from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from check_dated_transit import PLANNER, WEB, RouteService, find_key, minutes
from paired_transit import read, write
from amap_route import haversine
from day_facts import DayFacts, POI_TYPES

REGISTRY = WEB/"data/booking-sources.json"


def activities(version):
    return [(d["day"], a) for d in version["plan"]["itinerary"] for a in d["activities"]]


def official_links(rows, city, registry):
    names = set()
    for _, a in rows:
        name = a.get("position")
        proof = registry.get(city,{}).get(name)
        if not proof or proof["kind"] != a["_kind"]:
            continue
        if not proof.get("point") or (a.get("_pos") and haversine(proof["point"],a["_pos"]) <= .1):
            names.add(name)
    return len(names)


def targets(rows):
    transfers = []
    for day, a in rows:
        route = a["transports"]
        if not a.get("_lock") or not any(leg["mode"] == "metro" for leg in route):
            continue
        first, last = route[0], route[-1]
        if not first.get("_from") or not last.get("_to"):
            continue
        reserve = {"train":60, "airplane":120}.get(a["type"],0)
        deadline = minutes(a["start_time"])-reserve
        if deadline < minutes(first["start_time"]):
            continue
        transfers.append((deadline-minutes(last["end_time"]),day,a["start_time"],deadline,first,last))
    transfers.sort(key=lambda r:r[:3])
    place = next(((day,a) for day,a in rows if a.get("_pos") and a["_kind"] in POI_TYPES and
                  a["_kind"] == "attraction"),None)
    return transfers[:2], place


def latency(values):
    values = sorted(values)
    if not values:
        return None
    return {"p50":round(median(values),3),"p90":round(values[ceil(.9*len(values))-1],3)}


def observe(service, version, registry):
    now = perf_counter()
    calls = service.calls
    plan, archive = version["plan"], version["archive"]
    city = plan["target_city"]
    start = date.fromisoformat(version["startDate"])
    days = len(plan["itinerary"])
    service_facts = DayFacts(service)
    rows = activities(version)
    metro, place = targets(rows)
    baseline_checks = len(set(archive["pending"])) + 1 + len(metro) + int(place is not None)
    helped = Counter()
    details = {"official_links":official_links(rows,city,registry)}
    weather = service_facts.weather(city,start,days)
    forecast_days = sum(bool(d["day"] and d["night"]) for d in weather["days"])
    details.update(weather_days=forecast_days, weather_status=weather["status"], weather_requested_days=days)
    if forecast_days == days:
        helped["weather"] += 1
    routes = []
    for _, day, start_time, deadline, first, last in metro:
        on = (start + timedelta(days=day-1)).isoformat()
        leave = first["start_time"]
        route = service.route(first["_from"], last["_to"], "subway", city, departure=on+"T"+leave)
        if route.get("durationSeconds") and not route.get("unavailable"):
            margin = deadline-minutes(leave)-ceil(route["durationSeconds"]/60)
            routes.append({"day":day,"margin":margin,"target_time":start_time})
            if margin >= 0:
                helped["transfer"] += 1
        else:
            routes.append({"day":day,"reason":route.get("reason","no_timed_option")})
    details["metro"] = routes
    if place:
        day, a = place
        on = start+timedelta(days=day-1)
        data = service_facts.place(city,a["_kind"],a["position"],a["_pos"],on)
        details["poi"] = {"status":data["status"],"has_week":bool(data.get("week")),
                          "has_current":bool(data.get("today") and data.get("todayDate") == on.isoformat()),
                          "has_official":bool(data.get("official")), "reason":data.get("reason")}
        # Weekly hours and a site match help locate information but do not confirm that specific date.
        if data["status"] == "matched" and details["poi"]["has_current"]:
            helped["opening"] += 1
    return {"elapsed_s":round(perf_counter()-now,3),"calls":service.calls-calls,
            "manual_before":baseline_checks, "manual_after":baseline_checks-sum(helped.values()),
            "checks_completed":dict(helped),"details":details}


def main(run, out):
    status_rows = read(run/"SUMMARY.json")
    assert len(status_rows) == 30 and len({r["case"] for r in status_rows}) == 30
    registry = read(REGISTRY)
    key = find_key()
    if not key:
        raise ValueError("AMAP_KEY is required for the online arm")
    rows = []
    with TemporaryDirectory() as temp:
        service = RouteService(key, Path(temp), min_interval=1.30)
        for planning in status_rows:
            row = {k:planning.get(k) for k in ("case","level","outcome","first_s","final_s")}
            if row["outcome"] == "complete":
                result = read(run/row["case"]/"RESULT.json")
                version = next(v for v in result["group"]["versions"] if v["id"] == planning["candidate"])
                evidence = version["archive"]
                metrics = evidence["metrics"]
                row.update(candidate=version["id"], snapshot=evidence["snapshotId"],
                           validation_passed=evidence["validation"]["passed"],
                           metrics={k:metrics.get(k) for k in ("total_cost_cny","access_walk_minutes","city_transit_minutes","visit_minutes")},
                           preferences=dict(Counter(g["status"] for g in evidence["goals"])),
                           offline_calls=0, online=observe(service,version,registry))
            rows.append(row)
            print(f"{len(rows)}/30 {row['case']} {row['outcome']} online={row.get('online',{}).get('calls',0)}",flush=True)
    ok = [r for r in rows if "online" in r]
    write(out, {"planning_summary_sha256":sha256((run/"SUMMARY.json").read_bytes()).hexdigest(),
                "plan_outcomes":dict(Counter(r["outcome"] for r in rows)),
                "first_latency_s":latency([r["first_s"] for r in ok]),
                "online_latency_s":latency([r["online"]["elapsed_s"] for r in ok]),
                "additional_api_calls":sum(r["online"]["calls"] for r in ok),
                "manual_before":sum(r["online"]["manual_before"] for r in ok),
                "manual_after":sum(r["online"]["manual_after"] for r in ok),
                "cases":rows})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run",type=Path)
    parser.add_argument("out",type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("choose a new result file")
    main(args.run,args.out)
