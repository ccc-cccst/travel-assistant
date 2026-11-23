"""Regression checks for the experiment's measurements and interventions."""
from collections import Counter
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import (
    ExperimentAgent, PrefixProbeAgent, ProbeBudget, corrected_order, diversity,
)
from chinatravel.soft_constraints.pool import CandidatePool
import pandas as pd


def plan(*names):
    return {
        "people_number": 1, "start_city": "S", "target_city": "T",
        "itinerary": [{"day": 1, "activities": [
            {"type": "attraction", "position": name} for name in names
        ]}],
    }


def bare_agent():
    agent = PrefixProbeAgent.__new__(PrefixProbeAgent)
    agent.event_path = None
    agent.variant = "rank_fixed"
    agent.dfs_budget = 2
    agent.dfs_entries = agent.search_nodes = 0
    agent.wall_limit_seconds = None
    agent._depth = 0
    agent._path = []
    agent._active_branch = None
    agent.branches = []
    agent.first_rankings = []
    agent.discovery_curve = []
    agent.validation_depths = Counter()
    agent.pool = CandidatePool(20, lambda p: {"total_penalty": 1})
    agent.attractions_visiting = []
    import time
    agent.started = time.monotonic()
    return agent


class ProbeMeasurements(unittest.TestCase):
    def test_rank_sum_uses_item_ranks_instead_of_sorted_indices(self):
        self.assertEqual(
            corrected_order([30, 10, 20], [1, 2, 3], ["A", "B", "C"], []).tolist(),
            [1, 0, 2],
        )
        self.assertEqual(
            corrected_order([30, 10, 20], [1, 2, 3], ["A", "B", "C"], ["C"])[0],
            2,
        )

    def test_diversity_ignores_timing_changes_and_counts_empty_attraction_paths(self):
        a = plan("A", "B")
        b = copy.deepcopy(a)
        b["itinerary"][0]["activities"][0]["start_time"] = "09:30"
        result = diversity([{"plan": a}, {"plan": b}, {"plan": plan("C")}])
        self.assertEqual(result["unique_prefix_1"], 2)
        self.assertEqual(result["dominant_prefix_1_share"], 2 / 3)
        self.assertEqual(result["unique_route_skeletons"], 2)
        self.assertIsNone(diversity([])["dominant_prefix_1_share"])
        self.assertEqual(diversity([{"plan": plan()}])["prefix_1_counts"][0]["prefix"], [])

    def test_success_returning_false_is_still_recorded_as_added(self):
        agent = bare_agent()

        def collect(self, query, partial, poi_plan):
            self.pool.add(plan("A"))
            return False, partial

        with patch.object(ExperimentAgent, "constraints_validation", collect):
            valid, _ = agent.constraints_validation({}, plan("A")["itinerary"], {})
        self.assertFalse(valid)
        self.assertEqual(agent.validation_depths[("candidate_added", 1)], 1)
        self.assertEqual(agent.validation_depths[("not_added", 1)], 0)

    def test_recursive_budget_and_branch_accounting_are_not_double_counted(self):
        agent = bare_agent()

        def descend(self, query, poi_plan, partial, current_time, position, current_day=0):
            self.search_nodes += 1
            return self.dfs_poi(query, poi_plan, partial, current_time, position, current_day)

        with patch.object(ExperimentAgent, "dfs_poi", descend):
            with self.assertRaises(ProbeBudget):
                agent.dfs_poi({}, {}, plan("A")["itinerary"], "09:00", "A")
        self.assertEqual(agent.dfs_entries, 2)
        self.assertEqual(len(agent.branches), 1)
        self.assertEqual(agent.branches[0]["dfs_entries"], 2)
        self.assertEqual(agent.branches[0]["search_nodes"], 2)
        self.assertEqual(agent._depth, 0)
        self.assertIsNone(agent._active_branch)

    def test_root_intervention_preserves_other_candidates_and_leaves_deeper_nodes_alone(self):
        agent = bare_agent()
        agent.variant = "third_first"
        frame = pd.DataFrame({"name": ["A", "B", "C", "D"]})
        args = ([], "attraction", 0, "09:00", "S", frame, {}, [0, 1, 2, 3])
        with patch.object(
            ExperimentAgent, "reranking_attractions_with_constraints",
            return_value=[0, 1, 2, 3],
        ):
            self.assertEqual(agent.reranking_attractions_with_constraints(*args), [2, 0, 1, 3])
            agent.attractions_visiting = [2]
            self.assertEqual(agent.reranking_attractions_with_constraints(*args), [0, 1, 2, 3])
        self.assertEqual(len(agent.first_rankings), 1)


if __name__ == "__main__":
    unittest.main()
