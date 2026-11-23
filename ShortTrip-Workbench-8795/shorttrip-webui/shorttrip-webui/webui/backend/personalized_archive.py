"""Read a verified personalized planning run for the ShortTrip workbench."""

from __future__ import annotations

import json
from pathlib import Path

from export_plan import CityIndex, enrich


def _read(directory: Path, name: str) -> dict:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def _terminal(index: CityIndex, name: str) -> dict:
    point = index.places.get(name)
    return {"name": name, "city": index.city_cn,
            "pos": [point["lat"], point["lon"]] if point else None}


def _available(directory: Path, name: str) -> dict:
    try:
        return _read(directory, name)
    except FileNotFoundError:
        return {}


def load_archive(directory: Path, snapshot_roots: Path, *, final=True, job_id=None) -> dict | None:
    first = _available(directory, "WORKBENCH_FIRST.json")
    first_view = _available(directory, "FIRST_VIEW.json")
    if not final and not first:
        return None
    spec = _read(directory, "request.json")
    snapshot = _read(directory, "WORLD_FACTS.json")
    archive = _read(directory, "CANDIDATE_ARCHIVE.json") if final else {}
    assessment = _read(directory, "PLAN_ASSESSMENT.json") if final else {}
    result = _read(directory, "PLAN_VIEW.json") if final else {}
    if (any(source["snapshot_id"] != snapshot["snapshot_id"] for source in (archive, assessment, first) if source)
            or snapshot.get("request_revision") != spec.get("request_revision")
            or snapshot.get("request_id") != spec.get("request_id")):
        raise ValueError("归档、需求或核验记录版本不一致。")

    data = snapshot["data"]
    # Directory parts come from installed run files, not from a URL or request.
    commit, profile = data["commit"], data["profile"]
    if any(part in {".", ".."} or Path(part).name != part for part in (commit, profile)):
        raise ValueError("归档的数据版本名称无效。")
    database = snapshot_roots / commit[:7] / "sandbox_profiles" / profile / "database"
    if not database.is_dir():
        raise FileNotFoundError("缺少该归档生成时使用的旅行数据快照。")

    candidates = {entry["id"]: entry for entry in archive.get("archive", [])}
    checks = {entry["id"]: entry for entry in assessment.get("candidates", [])}
    first_id = (first or first_view).get("candidate", {}).get("id")
    if first:
        candidates.setdefault(first_id, first["candidate"])
        checks.setdefault(first_id, first["assessment"])
    recommended = result.get("recommended_id")
    alternatives = {value["id"]: value for value in result.get("alternatives", [])}
    if first_id not in candidates or not checks.get(first_id, {}).get("validation_report", {}).get("passed"):
        first_id = None
    ordered = tuple(dict.fromkeys(v for v in [recommended, *alternatives, first_id] if v))
    key = f"personalized-{job_id or snapshot['snapshot_id']}"
    title = f"{snapshot['origin']} → {snapshot['destination']} {snapshot['days']}日游"
    appointments = {(entry["value"]["day"], entry["value"]["name"], entry["value"]["start_time"])
                    for entry in spec["items"] if entry["field"] == "appointment"}
    origin = CityIndex(snapshot["origin"], database)
    destination = CityIndex(snapshot["destination"], database)
    versions = []
    for candidate_id in ordered:
        candidate = candidates[candidate_id]
        check = checks[candidate_id]
        if not check["validation_report"]["passed"]:
            raise ValueError("归档包含尚未通过独立核验的展示候选。")
        plan = enrich(candidate["itinerary"], database=database)
        places = {}
        legacy_places = {}
        for place in candidate.get("places", check.get("places", [])):
            place_key = (place["day"], place["name"])
            if "start_time" in place and "end_time" in place:
                places[(*place_key, place["start_time"], place["end_time"])] = place["id"]
            else:
                legacy_places.setdefault(place_key, set()).add(place["id"])
        moves = [a for day in plan["itinerary"] for a in day["activities"]
                 if a["type"] in ("train", "airplane")]
        for day in plan["itinerary"]:
            number = day["day"]
            for i, activity in enumerate(day["activities"]):
                uid = f"{candidate_id}-d{number}-a{i + 1}"
                place = activity.get("position")
                activity["_uid"] = uid
                activity["_poi_id"] = places.get((number, place, activity["start_time"], activity["end_time"]))
                legacy_ids = legacy_places.get((number, place), ())
                if activity["_poi_id"] is None and len(legacy_ids) == 1:
                    activity["_poi_id"] = next(iter(legacy_ids))
                for j, leg in enumerate(activity["transports"]):
                    leg["_id"] = f"{uid}-leg{j + 1}"
                if (number, place, activity["start_time"]) in appointments:
                    activity["_lock"] = "reservation"
                if activity["type"] in ("train", "airplane"):
                    returning = len(moves) > 1 and activity is moves[-1]
                    start, end = (destination, origin) if returning else (origin, destination)
                    activity["_terminals"] = [_terminal(start, activity["start"]), _terminal(end, activity["end"])]
                    terminal = activity["_terminals"][0 if returning else 1]
                    activity["_place"] = terminal["name"]
                    activity["_pos"] = terminal["pos"]
                    activity["_poi_id"] = f"terminal:{terminal['city']}:{terminal['name']}"
                    if returning:
                        activity["_lock"] = "departure"
        versions.append({
            "id": candidate_id, "title": title, "query": spec["raw_text"],
            "startDate": snapshot.get("requested_start_date"), "plan": plan,
            "archive": {
                "candidateId": candidate_id, "first": candidate_id == first_id,
                "recommended": candidate_id == recommended, "alternative": alternatives.get(candidate_id),
                "snapshot": data, "snapshotId": snapshot["snapshot_id"], "coordinateSystem": "GCJ-02",
                "requestId": spec.get("request_id"), "requestRevision": spec["request_revision"],
                "modelProvider": spec.get("model_provider"),
                "fromModelSketch": candidate.get("source") == "model",
                "requirements": spec["items"], "validation": check["validation_report"],
                "metrics": check["quality_vector"], "goals": check["goals"],
                "pending": check["pending_verifications"],
            },
        })
    return {"id": key, "title": title, "archive": True, "jobId": job_id, "recommendedId": recommended,
            "firstId": first_id, "versions": versions}
