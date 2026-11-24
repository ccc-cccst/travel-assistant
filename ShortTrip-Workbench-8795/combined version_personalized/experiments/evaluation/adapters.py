"""Import immutable legacy experiments or explicit publication-protocol manifests."""
from pathlib import Path

from .common import (HERE, PROJECT, clean_json, digest, file_digest, number, plan_fingerprint,
                     read_json, read_jsonl)


def requirement_for(case, query):
    entries = read_json(HERE / "requirements.v0.json")["cases"]
    entry = entries.get(case)
    if not entry:
        return {"reviewed": False, "items": [], "reason": "case_not_reviewed"}
    if entry.get("query_sha256") != digest(query):
        return {"reviewed": False, "items": [], "reason": "query_changed_since_review"}
    return entry


def snapshot_resources(snapshot, source_hash):
    resources = {"snapshot_sha256": source_hash}
    for table, data in snapshot.get("state", {}).get("memory", {}).items():
        rows = [dict(zip(data.get("columns", []), row)) for row in data.get("data", [])]
        key = "hotels" if table == "accommodations" else table
        resources[key] = {r.get("name", str(r.get("id"))): r for r in rows}
    return clean_json(resources)


def _parent_snapshot(path):
    for parent in (path, *path.parents):
        if (parent / "frozen.json").is_file():
            return parent / "frozen.json"
    raise ValueError(f"Cannot locate frozen.json for {path}")


def _candidates(path):
    if not path.exists():
        return []
    items = read_json(path)
    if not isinstance(items, list):
        raise ValueError(f"Expected candidate list: {path}")
    for item in items:
        actual = plan_fingerprint(item["plan"])
        if item.get("fingerprint", actual) != actual:
            raise ValueError(f"Candidate fingerprint mismatch in {path}")
        item["fingerprint"] = actual
    return items


def import_legacy(root):
    root = Path(root).resolve()
    paths = [root / "summary.json"] if (root / "summary.json").is_file() else sorted(root.rglob("summary.json"))
    runs = []
    for path in paths:
        summary = read_json(path)
        if "variant" not in summary or "frozen_sha256" not in summary:
            continue
        directory = path.parent
        frozen_path = _parent_snapshot(directory)
        source_hash = file_digest(frozen_path)
        if source_hash != summary["frozen_sha256"]:
            raise ValueError(f"Frozen source hash mismatch: {path}")
        frozen = read_json(frozen_path)
        case = summary["case"]
        query_path = PROJECT / "experiments/c_only/queries" / (case + ".json")
        if file_digest(query_path) != frozen["source_query_sha256"]:
            raise ValueError(f"Query source changed: {query_path}")
        query = read_json(query_path)
        candidates = _candidates(directory / "candidates.json")
        selected_path = directory / "selected.json"
        selected = read_json(selected_path) if selected_path.exists() else {}
        if selected and plan_fingerprint(selected) not in {c["fingerprint"] for c in candidates}:
            raise ValueError(f"Selected plan not present in candidate pool: {directory}")
        relative = directory.relative_to(root).as_posix()
        repetition = relative.rsplit("/", 1)[0] if "/" in relative else "1"
        runs.append({
            "run_id": relative or directory.name, "method": summary["variant"],
            "case_id": case, "query_id": query["uid"], "query": query,
            "query_sha256": digest(query), "requirements": requirement_for(case, query),
            "resources": snapshot_resources(frozen, source_hash), "resource_sha256": source_hash,
            "scope": "frozen_search", "protocol": "legacy_posthoc",
            "repetition": repetition, "order_id": "experiment_shared", "seed": frozen.get("seed"),
            "activity_policy": summary.get("activity_policy"),
            "budget": {"kind": "dfs_entries", "limit": summary["dfs_budget"],
                       "guard_seconds": summary["wall_limit_seconds"],
                       "candidate_limit": summary.get("candidate_limit")},
            "selected_plan": selected, "candidates": candidates, "events": [],
            "stop_reason": summary.get("stop_reason"), "executed": True,
            "planning_seconds": summary.get("elapsed_seconds"),
            "reported_first_candidate_seconds": (summary.get("first_candidate") or {}).get("elapsed_seconds"),
            "planner_model_calls": summary.get("llm_api_calls"),
            "planner_model_seconds": summary.get("actual_llm_seconds"),
            "legacy_penalty": summary.get("best_penalty"),
            "source": str(directory),
            "source_hashes": {str(p): file_digest(p) for p in
                              (path, selected_path, directory / "candidates.json", frozen_path, query_path) if p.exists()},
            "measurement_issues": ["legacy_has_no_publication_timestamps"],
        })
    if not runs:
        raise ValueError(f"No compatible legacy runs under {root}")
    return runs


def import_manifest(path):
    path = Path(path).resolve()
    manifest = read_json(path)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("runs"), list):
        raise ValueError("Expected schema_version=1 and runs list")
    result = []
    for entry in manifest["runs"]:
        row = dict(entry)
        def load(key, default):
            value = entry.get(key, default)
            if isinstance(value, str):
                source = (path.parent / value).resolve()
                row.setdefault("source_hashes", {})[str(source)] = file_digest(source)
                return read_jsonl(source) if source.suffix == ".jsonl" else read_json(source)
            return value
        for key in ("run_id", "method", "case_id", "scope", "protocol", "budget", "repetition", "order_id"):
            if key not in row:
                raise ValueError(f"Manifest run missing {key}")
        if row["protocol"] not in {"published_v1", "legacy_posthoc"}:
            raise ValueError("Unsupported protocol")
        row["query"] = load("query", {})
        row["query_id"] = row["query"].get("uid", row["case_id"])
        row["query_sha256"] = digest(row["query"])
        row["resources"] = clean_json(load("resources", {}))
        row["resource_sha256"] = digest(row["resources"])
        row["requirements"] = load("requirements", requirement_for(row["case_id"], row["query"]))
        row["selected_plan"] = load("selected_plan", {})
        row["candidates"] = load("candidates", [])
        for c in row["candidates"]:
            actual = plan_fingerprint(c["plan"])
            if c.get("fingerprint", actual) != actual:
                raise ValueError("Candidate fingerprint mismatch")
            c["fingerprint"] = actual
        row["events"] = load("events", [])
        row["executed"] = entry.get("executed", True)
        row["source"] = str(path)
        row.setdefault("measurement_issues", [])
        row.setdefault("source_hashes", {})[str(path)] = file_digest(path)
        if row["protocol"] == "published_v1":
            if row["budget"].get("kind") != "wall_seconds" or not number(row["budget"].get("limit")) or row["budget"]["limit"] <= 0:
                raise ValueError("published_v1 requires positive wall_seconds budget")
        result.append(row)
    ids = [r["run_id"] for r in result]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate run_id in manifest")
    if not result:
        raise ValueError("Empty run manifest")
    return result


def publication_state(run):
    """Determine what was published by B; never use a later selected.json as fallback."""
    if run["protocol"] == "legacy_posthoc":
        return {"admission": "legacy_posthoc", "selected_plan": run.get("selected_plan", {}),
                "publications": [], "measurement_status": "not_applicable"}
    issues = []
    previous = -1.0
    events = run.get("events", [])
    if not events or events[0].get("event") != "run_started":
        issues.append("missing_run_started")
    index = {plan_fingerprint(c["plan"]): c["plan"] for c in run.get("candidates", [])}
    selected = run.get("selected_plan")
    if selected:
        index[plan_fingerprint(selected)] = selected
    publications, finals = [], []
    terminal = None
    starts = 0
    for event in events:
        stamp = event.get("at_seconds")
        if not number(stamp) or stamp < previous or stamp < 0:
            issues.append("invalid_or_nonmonotonic_event_timestamp")
            continue
        previous = stamp
        if terminal is not None:
            issues.append("events_after_terminal")
            continue
        if event.get("event") == "run_started":
            starts += 1
            if starts != 1:
                issues.append("duplicate_run_started")
        if event.get("event") in {"run_finished", "hard_timeout"}:
            terminal = event
            continue
        if event.get("event") == "final_published":
            if not publications or event.get("fingerprint") != publications[-1]["fingerprint"]:
                issues.append("final_fingerprint_not_current_incumbent")
            else:
                finals.append(event)
            continue
        if event.get("event") != "incumbent_published":
            continue
        plan = event.get("plan") or index.get(event.get("fingerprint"))
        if not plan or event.get("fingerprint") != plan_fingerprint(plan):
            issues.append("publication_plan_missing_or_fingerprint_mismatch")
            continue
        publications.append({"at_seconds": stamp, "plan": plan, "fingerprint": event["fingerprint"]})
    if terminal is None:
        issues.append("missing_terminal_event")
    deadline = run["budget"]["limit"]
    before = [p for p in publications if p["at_seconds"] <= deadline]
    return {"admission": "published_v1", "selected_plan": before[-1]["plan"] if before else {},
            "publications": publications, "measurement_status": "unknown" if issues else "known",
            "measurement_issues": issues, "selected_at_seconds": before[-1]["at_seconds"] if before else None,
            "first_result_late": not bool(before) if not issues else None,
            "final_delivery_late": (not finals or finals[-1]["at_seconds"] > deadline) if not issues else None,
            "stop_reason": terminal.get("stop_reason", "hard_timeout" if terminal.get("event") == "hard_timeout" else "unknown") if terminal else None,
            "planning_seconds": terminal.get("at_seconds") if terminal else None}
