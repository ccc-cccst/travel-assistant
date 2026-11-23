"""Freeze independent route endpoints, compare AMap timings, then trial earlier departures."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from check_dated_transit import PLANNER, ROOT, RouteService, find_key, load_archive, minutes
from amap_route import RouteError

sys.path.insert(0, str(PLANNER))
RESULT = PLANNER / "result"
OUTPUT = RESULT / "experiments/P049_paired_transit"
SNAPSHOTS = ROOT / "ShortTrip/artifacts/data"
LAST_PILOT = RESULT / "experiments/P048_dated_transit/live_01.json"
APPOINTMENTS = [
    ("P039_full_pace/constraints_01/fixed_appointment/default", "c9a1e55f4f2426be1922f95f"),
    ("P014_integrated_planning/live_appointment", "eef1a7079151144aa5ef9aaa"),
    ("P014_integrated_planning/smoke_01/four_day_appointment", "c4e782726d0c9a265a0e55ef"),
    ("P039_full_pace/constraints_01/fixed_appointment/default", "37c7fd413a7b5f0f546b1299"),
    ("P014_integrated_planning/live_appointment", "12e2262b1de6772bc460ca06"),
    ("P014_integrated_planning/smoke_01/four_day_appointment", "1b23f1c360b7437cb889c8d1"),
]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidates(folder, cid):
    spec = read(folder / "request.json")
    appts = {(v["value"]["day"], v["value"]["name"], v["value"]["start_time"])
             for v in spec["items"] if v["field"] == "appointment"}
    archive = read(folder / "CANDIDATE_ARCHIVE.json")["archive"]
    plan = next(c["itinerary"] for c in archive if c["id"] == cid)
    for day in plan["itinerary"]:
        for i, a in enumerate(day["activities"]):
            legs = a.get("transports") or []
            if not legs or not i:
                continue
            start, end = legs[0], legs[-1]
            if start["start"] == end["end"]:
                continue
            if (day["day"], a.get("position"), a["start_time"]) in appts:
                category = "appointment"
            elif a["type"] in ("airplane", "train"):
                if day["day"] != len(plan["itinerary"]) or i != len(day["activities"]) - 1:
                    continue
                category = "return"
            elif a["type"] in ("attraction", "lunch", "dinner"):
                category = "ordinary"
            else:
                continue
            reserve = {"train": 60, "airplane": 120}.get(a["type"], 0)
            modes = {l["mode"] for l in legs}
            mode = "metro" if "metro" in modes else start["mode"]
            yield dict(source=folder.relative_to(RESULT).as_posix(), candidate=cid, category=category,
                       city=plan["target_city"], day=day["day"], days=len(plan["itinerary"]), slot=i+1,
                       origin=start["start"], destination=end["end"], clock=start["start_time"],
                       previous_end=day["activities"][i-1]["end_time"], anchor=a["start_time"], reserve=reserve,
                       archived_mode=mode, archived_minutes=minutes(end["end_time"])-minutes(start["start_time"]),
                       archived_spare=minutes(a["start_time"])-reserve-minutes(end["end_time"]))


def freeze():
    from chinatravel.personalization.cli import DATA, environment
    from chinatravel.personalization.world_facts import WorldFacts
    environment(DATA)
    facts = WorldFacts.configured(DATA)
    used = {(x["origin"], x["destination"]) for x in read(LAST_PILOT)["cases"]}
    rows = []
    for folder, cid in APPOINTMENTS:
        row, = [r for r in candidates(RESULT/"experiments"/folder, cid) if r["category"] == "appointment"]
        rows.append(row)
        used.add((row["origin"], row["destination"]))
    pool = []
    for file in sorted((RESULT/"manual").glob("*/PLAN_VIEW.json")):
        cid = read(file).get("recommended_id")
        if cid:
            pool.extend(r for r in candidates(file.parent, cid) if r["days"] <= 3 and r["archived_mode"] == "metro")
    for category in ("return", "ordinary"):
        chosen = []
        for i in range(6):
            options = [r for r in pool if r["category"] == category and (r["origin"], r["destination"]) not in used]
            def order(r):
                return (sum(c["city"] == r["city"] for c in chosen),
                        r["archived_spare"] * (1 if i % 2 == 0 else -1),
                        sum(c["source"] == r["source"] for c in chosen), r["source"], r["slot"])
            row = min(options, key=order)
            chosen.append(row)
            used.add((row["origin"], row["destination"]))
        rows.extend(chosen)
    groups = {}
    for i, row in enumerate(rows):
        folder = RESULT / row["source"]
        if folder not in groups:
            groups[folder] = load_archive(folder, SNAPSHOTS)
        version = next(v for v in groups[folder]["versions"] if v["id"] == row["candidate"])
        a = version["plan"]["itinerary"][row["day"]-1]["activities"][row["slot"]-1]
        legs = a["transports"]
        row.update(id=f"S{i+1:02}", date=["2026-11-23","2026-11-28"][i % 2],
                   frm=legs[0]["_from"], to=legs[-1]["_to"], local_minutes=row["archived_minutes"])
        assert row["frm"] and row["to"] and not version["startDate"]
        if row["archived_mode"] != "metro":
            from chinatravel.personalization.planning_compat import constructor_input
            req, _ = constructor_input(read(folder/"request.json"), snapshot=read(folder/"WORLD_FACTS.json"))
            assert "metro" in req.get("local_modes", ["metro"])
            route = facts.route(row["city"], row["origin"], row["destination"], "metro", row["clock"], req["people_number"])
            row["local_minutes"] = route["duration_minutes"] if any(l["mode"] == "metro" for l in route["legs"]) else None
    assert len({(r["origin"], r["destination"]) for r in rows}) == 18
    write(OUTPUT/"samples.json", rows)


def query(service, row, departure):
    before = perf_counter()
    try:
        if departure:
            r = service.route(row["frm"], row["to"], "subway", row["city"], departure=departure)
        else:
            # Use the same route parser and strategy, with no ambient route-cache hit or disk write.
            r = service._transit(row["frm"], row["to"], row["city"], subway=True)
        sec = r.get("durationSeconds")
        result = {"minutes": math.ceil(sec/60)} if sec else {"reason":r.get("reason") or "no_timed_subway_option"}
    except RouteError as exc:
        result = {"reason":str(exc)}
    return {**result, "seconds":round(perf_counter()-before, 3)}


def compare(service, output):
    rows = []
    for i, r in enumerate(read(OUTPUT/"samples.json")):
        lookup = {"dated":r["date"]+"T"+r["clock"], "undated":None}
        order = ("dated","undated") if i%2 else ("undated","dated")
        obs = {k:query(service, r, lookup[k]) for k in order}
        deadline = minutes(r["anchor"])-r["reserve"]-minutes(r["clock"])
        obs.update(id=r["id"], available_minutes=deadline)
        rows.append(obs)
    write(output, {"manifest_sha256":checksum(OUTPUT/"samples.json"), "calls":service.calls, "observations":rows})


def recover_limits(service, comparison, output):
    baseline = read(comparison)
    assert baseline["manifest_sha256"] == checksum(OUTPUT/"samples.json")
    retried = []
    for row, obs in zip(read(OUTPUT/"samples.json"), baseline["observations"], strict=True):
        assert row["id"] == obs["id"]
        for k, timestamp in (("undated", None), ("dated", row["date"]+"T"+row["clock"])):
            if "(10021)" in obs[k].get("reason",""):
                obs[k] = query(service, row, timestamp)
                retried.append([row["id"], k])
    write(output, {**baseline, "first_pass_sha256":checksum(comparison),
                   "retried":retried, "additional_calls":service.calls})


def repair(service, comparison, output):
    from chinatravel.personalization.cli import DATA, environment
    from chinatravel.personalization.itinerary_edit import edit_preview
    from chinatravel.personalization.world_facts import WorldFacts
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    output = output.resolve()
    inputs = read(comparison)
    assert inputs["manifest_sha256"] == checksum(OUTPUT/"samples.json")
    rows = []
    for r, obs in zip(read(OUTPUT/"samples.json"), inputs["observations"], strict=True):
        assert r["id"] == obs["id"]
        estimate = obs["dated"].get("minutes")
        if r["category"] == "ordinary" or estimate is None or estimate <= obs["available_minutes"]:
            continue
        item = {"id":r["id"], "deficit":estimate-obs["available_minutes"]}
        rows.append(item)
        if r["archived_mode"] != "metro":
            item["state"] = "comparison_mode_only"
            continue
        old = minutes(r["clock"])
        start = max(minutes(r["previous_end"]), old - item["deficit"] - 3)
        if old-start < item["deficit"]:
            item["state"] = "insufficient_idle_time"
            continue
        if service.calls >= 6:
            item["state"] = "query_budget_exhausted"
            continue
        clock = f"{start//60:02}:{start%60:02}"
        item["suggested"] = clock
        online = query(service,r,r["date"]+"T"+clock)
        item["new_lookup"] = online
        if "minutes" not in online:
            item["state"] = "unverified"
            continue
        item["new_spare"] = minutes(r["anchor"])-r["reserve"]-start-online["minutes"]
        if item["new_spare"] < 0:
            item["state"] = "still_tight"
            continue
        trial = output.parent / (output.stem+"_previews") / r["id"]
        op = {"action":"advance_transfer","day":r["day"],"slot":r["slot"],"start":clock}
        status = edit_preview(RESULT/r["source"],trial,r["candidate"],op,env,facts)
        item.update(state=status["state"], reason=status["message"])
        if status["state"] == "complete":
            item.update(edit=status["edit"], trial=trial.relative_to(RESULT).as_posix())
    write(output, {"comparison_sha256":checksum(comparison), "additional_calls":service.calls, "trials":rows})


def audit(repairs, output):
    from copy import deepcopy
    def without_visit_times(obj):
        if isinstance(obj, list):
            return [without_visit_times(x) for x in obj]
        if isinstance(obj, dict):
            return {k:without_visit_times(v) for k,v in obj.items() if k not in ("start_time","end_time")}
        return obj

    samples = {s["id"]:s for s in read(OUTPUT/"samples.json")}
    audits = []
    for trial in read(repairs)["trials"]:
        if trial["state"] != "complete":
            continue
        sample = samples[trial["id"]]
        source, updated = RESULT/sample["source"], RESULT/trial["trial"]
        before = next(c for c in read(source/"CANDIDATE_ARCHIVE.json")["archive"] if c["id"] == sample["candidate"])
        old_check = next(c for c in read(source/"PLAN_ASSESSMENT.json")["candidates"] if c["id"] == sample["candidate"])
        after, = read(updated/"CANDIDATE_ARCHIVE.json")["archive"]
        check, = read(updated/"PLAN_ASSESSMENT.json")["candidates"]
        # Compare all original activity fields and every unrelated edge, including locks and visit times.
        old_days = before["itinerary"]["itinerary"]
        new_days = deepcopy(after["itinerary"]["itinerary"])
        i, j = sample["day"]-1, sample["slot"]-1
        new_days[i]["activities"][j]["transports"] = old_days[i]["activities"][j]["transports"]
        audited = dict(id=sample["id"], passed=check["validation_report"]["passed"],
                       all_other_edges_and_activities_equal=new_days==old_days,
                       preference_evidence_equal=without_visit_times(old_check["goals"])==without_visit_times(check["goals"]),
                       metric_changes={k:round(check["quality_vector"][k]-old_check["quality_vector"][k],2)
                          for k in ("total_cost_cny","city_transit_minutes","access_walk_minutes","visit_minutes")})
        audits.append(audited)
    write(output, {"repairs_sha256":checksum(repairs), "audits":audits})


def chengdu_tradeoff(service, repairs, output):
    from chinatravel.personalization.cli import DATA, environment
    from chinatravel.personalization.itinerary_edit import edit_preview
    from chinatravel.personalization.world_facts import WorldFacts
    row = next(x for x in read(OUTPUT/"samples.json") if x["id"] == "S12")
    prior = next(x for x in read(repairs)["trials"] if x["id"] == "S12")
    assert prior["state"] == "insufficient_idle_time"
    source = RESULT/row["source"]
    before = next(x for x in read(source/"CANDIDATE_ARCHIVE.json")["archive"] if x["id"] == row["candidate"])
    stop = before["itinerary"]["itinerary"][row["day"]-1]["activities"][row["slot"]-2]
    assert stop["position"] == "摩诃池" and stop["end_time"] == "09:57"
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    result = {"id":"S12", "previous_end":"09:57", "proposed_end":"09:32"}
    base = output.resolve().parent/(output.stem+"_previews")
    first, second = base/"visit", base/"transfer"
    edit = edit_preview(source, first, row["candidate"], {"action":"time","start":stop["start_time"],
                        "end":"09:32","day":row["day"],"slot":row["slot"]-1}, env, facts)
    result.update(visit_edit=edit["state"], reason=edit["message"])
    if edit["state"] == "complete":
        revised = edit_preview(first,second,read(first/"PLAN_VIEW.json")["recommended_id"],
                        {"action":"advance_transfer","day":row["day"],"slot":row["slot"],"start":"09:32"}, env,facts)
        result.update(transfer_edit=revised["state"], reason=revised["message"])
        if revised["state"] == "complete":
            lookup = query(service, row, row["date"]+"T09:32")
            old = edit["edit"]["before"]
            new = revised["edit"]["after"]
            result.update(lookup=lookup, metric_changes={k:round(new[k]-old[k],2) for k in
                           ("total_cost_cny","city_transit_minutes","access_walk_minutes","visit_minutes")})
            if "minutes" in lookup:
                result["new_spare"] = minutes(row["anchor"])-row["reserve"]-minutes("09:32")-lookup["minutes"]
    result["additional_calls"] = service.calls
    write(output, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["freeze","compare","recover","repair","audit","chengdu"])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--comparison", type=Path)
    args = parser.parse_args()
    if args.stage == "freeze":
        freeze()
    elif args.stage == "audit":
        if not args.out or not args.comparison:
            parser.error("audit requires --comparison pointing to a repair result and a new --out")
        audit(args.comparison, args.out)
    else:
        if not args.out or args.out.exists() or (args.stage in ("recover","repair","chengdu") and not args.comparison):
            parser.error("choose a fresh --out and specify --comparison for repair")
        key = find_key()
        if not key:
            parser.error("configure AMAP_KEY in the backend environment")
        with TemporaryDirectory() as tmp:
            service = RouteService(key, Path(tmp), min_interval=0.12 if args.stage == "compare" else 1.3)
            if args.stage == "compare":
                compare(service, args.out)
            elif args.stage == "recover":
                recover_limits(service, args.comparison, args.out)
            elif args.stage == "chengdu":
                chengdu_tradeoff(service, args.comparison, args.out)
            else:
                repair(service, args.comparison, args.out)
