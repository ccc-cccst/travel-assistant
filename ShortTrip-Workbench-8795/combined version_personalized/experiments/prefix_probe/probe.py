"""Instrumentation and controlled interventions for the early-POI experiment."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "experiments/c_only"))

from experiment_agent import ExperimentAgent
from rules import WINDOWS, clock
from chinatravel.data.load_datasets import NpEncoder


def freeze(value):
    return json.loads(json.dumps(value, ensure_ascii=False, cls=NpEncoder))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, cls=NpEncoder),
        encoding="utf-8",
    )
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def activities(plan):
    days = plan.get("itinerary", []) if isinstance(plan, dict) else plan
    return [a for day in days for a in day.get("activities", [])]


def attraction_prefix(plan, length=2):
    return tuple(
        str(a["position"]) for a in activities(plan) if a["type"] == "attraction"
    )[:length]


def diversity(candidates):
    result = {}
    for length in (1, 2):
        counts = Counter(attraction_prefix(c["plan"], length) for c in candidates)
        result[f"unique_prefix_{length}"] = len(counts)
        result[f"dominant_prefix_{length}_share"] = (
            max(counts.values()) / len(candidates) if candidates else None
        )
        result[f"prefix_{length}_counts"] = [
            {"prefix": list(prefix), "count": count}
            for prefix, count in counts.most_common()
        ]
    skeletons = {
        tuple((a["type"], a.get("position")) for a in activities(c["plan"]))
        for c in candidates
    }
    result["unique_route_skeletons"] = len(skeletons)
    return result


def corrected_order(prices, distances, names, recommendations):
    """Convert sorted indices to per-item ranks before combining them."""
    price_order = np.argsort(np.asarray(prices))
    distance_order = np.argsort(np.asarray(distances))
    price_rank = np.empty(len(names), dtype=int)
    distance_rank = np.empty(len(names), dtype=int)
    price_rank[price_order] = np.arange(len(names))
    distance_rank[distance_order] = np.arange(len(names))
    # Retain the original recommendation priority, including its scale.
    for rank, name in enumerate(recommendations):
        for idx, candidate_name in enumerate(names):
            if name == candidate_name:
                price_rank[idx] = distance_rank[idx] = -len(recommendations) + rank
    return np.argsort(price_rank + distance_rank)


class ProbeBudget(Exception):
    pass


class PrefixProbeAgent(ExperimentAgent):
    def __init__(self, *, variant, dfs_budget, activity_policy, event_path=None, **kwargs):
        super().__init__(group="C", **kwargs)
        self.variant = variant
        self.dfs_budget = dfs_budget
        self.activity_policy = activity_policy
        self.event_path = Path(event_path) if event_path else None
        self.dfs_entries = 0
        self._depth = 0
        self._path = []
        self._active_branch = None
        self.branches = []
        self.discovery_curve = []
        self.first_rankings = []
        self.prune_depths = Counter()
        self.validation_depths = Counter()
        self.started = time.monotonic()

    def emit(self, kind, **data):
        if self.event_path:
            with self.event_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(
                    {"event": kind, "dfs_entries": self.dfs_entries,
                     "search_nodes": self.search_nodes,
                     "elapsed_seconds": time.monotonic() - self.started, **data},
                    ensure_ascii=False, cls=NpEncoder,
                ) + "\n")

    def _deadline_check(self):
        super()._deadline_check()
        if self.dfs_entries >= self.dfs_budget:
            raise ProbeBudget("dfs_budget_reached")

    def dfs_poi(self, query, poi_plan, plan, current_time, current_position, current_day=0):
        self._deadline_check()
        self.dfs_entries += 1
        old_path = self._path
        self._path = freeze(plan)
        self._depth += 1
        prefix = attraction_prefix(plan, 1)
        owns_branch = bool(prefix and self._active_branch is None)
        if owns_branch:
            branch = {
                "first_attraction": prefix[0], "day": current_day + 1,
                "entry_time": current_time, "entry_position": current_position,
                "dfs_start": self.dfs_entries, "nodes_start": self.search_nodes,
                "candidate_start": len(self.pool.items),
                "elapsed_start": time.monotonic() - self.started,
            }
            self._active_branch = branch
            self.emit("branch_enter", **branch)
        self.emit("dfs_enter", depth=self._depth,
                  activity_depth=len(activities(plan)), prefix=list(prefix),
                  day=current_day + 1, current_time=current_time,
                  position=current_position)
        try:
            return super().dfs_poi(
                query, poi_plan, plan, current_time, current_position, current_day,
            )
        finally:
            if owns_branch:
                branch.update(
                    dfs_entries=self.dfs_entries - branch["dfs_start"] + 1,
                    search_nodes=self.search_nodes - branch["nodes_start"],
                    candidates_added=len(self.pool.items) - branch["candidate_start"],
                    elapsed_seconds=time.monotonic() - self.started - branch["elapsed_start"],
                )
                self.branches.append(branch)
                self.emit("branch_exit", **branch)
                self._active_branch = None
            self._depth -= 1
            self._path = old_path

    def record_prune(self, rejected):
        super().record_prune(rejected)
        depth = len(activities(self._path))
        self.prune_depths[(rejected["rule"], depth)] += 1
        self.emit("prune", activity_depth=depth, prefix=list(attraction_prefix(self._path)),
                  reason=freeze(rejected))

    def constraints_validation(self, query, plan, poi_plan):
        before = len(self.pool.items)
        try:
            return super().constraints_validation(query, plan, poi_plan)
        finally:
            # A legal candidate deliberately returns False in collection mode.
            added = len(self.pool.items) > before
            depth = len(activities(plan))
            self.validation_depths[("candidate_added" if added else "not_added", depth)] += 1
            self.emit("complete_validation", activity_depth=depth, candidate_added=added,
                      prefix=list(attraction_prefix(plan)))

    def candidate_found(self, pool):
        best = pool.best()
        point = {
            "candidate_count": len(pool.items), "dfs_entries": self.dfs_entries,
            "search_nodes": self.search_nodes,
            "elapsed_seconds": time.monotonic() - self.started,
            "best_penalty": best["score"]["total_penalty"],
            "candidate_penalty": pool.items[-1]["score"]["total_penalty"],
            "prefix": list(attraction_prefix(pool.items[-1]["plan"])),
        }
        self.discovery_curve.append(point)
        self.emit("candidate_added", **point)

    def select_next_poi_type(self, candidates_type, plan, poi_plan,
                             current_day, current_time, current_position):
        if self.activity_policy == "qwen":
            return super().select_next_poi_type(
                candidates_type, plan, poi_plan, current_day, current_time, current_position,
            )
        # Identical, deterministic activity policy in every mechanism-test arm.
        missing = self.missing_today(plan, poi_plan, current_day)
        required = {o["kind"] for o in missing}
        candidates_type[:] = [
            t for t in candidates_type if t not in WINDOWS or t in required
        ]
        if missing:
            candidates_type[:] = [
                t for t in candidates_type if t not in {"hotel", "back-intercity-transport"}
            ]
        if not candidates_type:
            return "attraction", []
        now = current_day * 1440 + clock(current_time)
        for obligation in sorted(missing, key=lambda o: o["window"][1]):
            if obligation["kind"] in candidates_type and now >= obligation["window"][0]:
                return obligation["kind"], candidates_type
        if "back-intercity-transport" in candidates_type:
            if clock(poi_plan["back_transport"]["BeginTime"]) - clock(current_time) <= 180:
                return "back-intercity-transport", candidates_type
        if "hotel" in candidates_type and clock(current_time) >= 1200:
            return "hotel", candidates_type
        return ("attraction" if "attraction" in candidates_type else candidates_type[0]), candidates_type

    def ranking_attractions(self, plan, poi_plan, current_day, current_time,
                            current_position, intercity_with_hotel_cost):
        if self.variant == "original":
            return super().ranking_attractions(
                plan, poi_plan, current_day, current_time,
                current_position, intercity_with_hotel_cost,
            )
        rows = self.memory["attractions"]
        distances = []
        for _, row in rows.iterrows():
            if row["name"] == current_position:
                distances.append(0)
            else:
                routes = self.collect_innercity_transport(
                    self.query["target_city"], current_position, row["name"],
                    current_time, "walk",
                )
                # Match the original route handling; change only rank composition.
                distances.append(routes[0]["distance"])
        return corrected_order(
            rows["price"].values, distances, rows["name"].tolist(),
            self.suggested_attractions_from_query,
        )

    def reranking_attractions_with_constraints(self, plan, poi_type, current_day,
                                               current_time, current_position,
                                               attr_info, query, ranking_attractions):
        order = list(super().reranking_attractions_with_constraints(
            plan, poi_type, current_day, current_time, current_position,
            attr_info, query, ranking_attractions,
        ))
        if not self.attractions_visiting:
            before = [str(attr_info.iloc[int(i)]["name"]) for i in order]
            offset = {"second_first": 1, "third_first": 2}.get(self.variant, 0)
            # Change only the leading candidate; retain the order of all others.
            if offset < len(order) and offset:
                order = [order[offset], *order[:offset], *order[offset + 1:]]
            record = {
                "day": current_day + 1, "time": current_time,
                "position": current_position, "original_top": before[:10],
                "actual_top": [str(attr_info.iloc[int(i)]["name"]) for i in order[:10]],
            }
            self.first_rankings.append(record)
            self.emit("first_attraction_order", **record)
        return order
