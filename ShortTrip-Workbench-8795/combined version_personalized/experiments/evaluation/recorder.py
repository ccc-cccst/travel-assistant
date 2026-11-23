"""Opt-in publication evidence for future planners; never chooses a plan itself."""
import json
import time
from pathlib import Path

from .common import canonical, plan_fingerprint, write_json


class RunRecorder:
    """Call publish only after the planner has finished validating/choosing a plan.

    The clock covers initialization after construction, online scoring, and IO.
    This helper is not automatically attached to the production DFS.
    """
    def __init__(self, directory, *, query, method, case_id, budget_seconds,
                 scope="frozen_search", resources=None, requirements=None,
                 repetition=1, order_id="default", seed=0, clock=time.monotonic):
        if budget_seconds <= 0:
            raise ValueError("budget_seconds must be positive")
        self.clock, self.started = clock, clock()
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.items, self.current, self.closed = [], {}, False
        self.event_path = self.directory / "events.jsonl"
        write_json(self.directory / "selected.json", {})
        write_json(self.directory / "candidates.json", [])
        self.entry = {"run_id": self.directory.name, "method": method, "case_id": case_id,
                      "query": query, "resources": resources or {}, "requirements": requirements,
                      "scope": scope, "protocol": "published_v1", "executed": True,
                      "budget": {"kind": "wall_seconds", "limit": budget_seconds},
                      "repetition": repetition, "order_id": order_id, "seed": seed,
                      "events": str(self.event_path), "selected_plan": str(self.directory / "selected.json"),
                      "candidates": str(self.directory / "candidates.json")}
        self.event("run_started")
        write_json(self.directory / "manifest.json", {"schema_version": 1, "runs": [self.entry]})

    def event(self, kind, **payload):
        if self.closed:
            raise RuntimeError("Recorder already finished")
        if {"event", "at_seconds"} & payload.keys():
            raise ValueError("Event identity/time cannot be overridden")
        row = {"event": kind, "at_seconds": self.clock()-self.started, **payload}
        with self.event_path.open("a", encoding="utf-8") as stream:
            stream.write(canonical(row)+"\n")
            stream.flush()
        return row

    def publish(self, plan):
        if self.closed:
            raise RuntimeError("Recorder already finished")
        plan = json.loads(canonical(plan))  # DFS may mutate/backtrack its own plan after publication.
        fingerprint = plan_fingerprint(plan)
        if fingerprint not in {i["fingerprint"] for i in self.items}:
            self.items.append({"fingerprint": fingerprint, "plan": plan, "ordinal": len(self.items)+1})
            write_json(self.directory / "candidates.json", self.items)
        write_json(self.directory / "selected.json", plan)
        self.current = plan
        # Timestamp after the snapshot is committed: no pre-IO timestamp credit.
        return self.event("incumbent_published", fingerprint=fingerprint)

    def finish(self, stop_reason="search_exhausted"):
        if self.current:
            self.event("final_published", fingerprint=plan_fingerprint(self.current))
        self.entry["stop_reason"] = stop_reason
        last = self.event("run_finished", stop_reason=stop_reason)
        self.entry["planning_seconds"] = last["at_seconds"]
        write_json(self.directory / "manifest.json", {"schema_version": 1, "runs": [self.entry]})
        self.closed = True
