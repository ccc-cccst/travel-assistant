"""Calibration workflow tests use synthetic test labels, never real annotations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.evaluation.calibration import export_blind_pairs, evaluate_labels


def record(query_id="q1", index=0, score=50, profile="full_v0"):
    return {
        "query_id": query_id,
        "query": {"nature_language": "两人轻松逛博物馆，考虑午餐与接驳。", "method": "hidden-query-method"},
        "plan": {"people_number": 2, "start_city": "甲城", "target_city": "乙城",
                 "method": "hidden-plan-method", "score": 12345,
                 "itinerary": [{"day": 1, "activities": [
                     {"type": "attraction", "position": f"博物馆{index}", "start_time": "09:30", "end_time": "11:00",
                      "cost": 40, "tickets": 2, "transports": [
                          {"mode": "metro", "start": "酒店", "end": "博物馆", "start_time": "09:00", "end_time": "09:25", "distance": 5, "cost": 8}]},
                     {"type": "lunch", "position": "本地菜馆", "start_time": "11:15", "end_time": "12:15", "cost": 100}
                 ]}]},
        "fingerprint": f"{query_id}-fingerprint-{index}", "quality_score": score,
        "preference_score": score, "composite_score": score,
        "method": f"secret-algorithm-{index}", "profile": profile,
    }


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def export(self, records=None, name="review", **kwargs):
        return export_blind_pairs(records or [record(index=0, score=25), record(index=1, score=75)],
                                  self.root / name, **kwargs)

    def key(self, name="review"):
        return self.root / name / "private_key.json"

    def labels(self, rows, filename="labels.jsonl"):
        path = self.root / filename
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def test_public_review_hides_method_scores_and_retains_itinerary(self):
        metadata = self.export()
        review = (self.root / "review/blind_review.md").read_text(encoding="utf-8")
        for hidden in ("secret-algorithm", "hidden-query-method", "hidden-plan-method", "quality_score", "composite_score", "12345", "fingerprint"):
            self.assertNotIn(hidden, review)
        for expected in ("09:30", "11:00", "09:00", "09:25", "metro", "酒店", "本地菜馆", "费用：100"):
            self.assertIn(expected, review)
        private = self.key().read_text(encoding="utf-8")
        self.assertIn("secret-algorithm", private)
        self.assertEqual(metadata["share_files"], ["blind_review.md", "labels.template.jsonl"])

    def test_unfilled_labels_never_become_ground_truth(self):
        self.export()
        template = self.root / "review/labels.template.jsonl"
        rows = [json.loads(line) for line in template.read_text().splitlines()]
        self.assertTrue(all(row["winner"] is None for row in rows))
        result = evaluate_labels(self.key(), template)
        self.assertEqual(result["overall"]["annotation_coverage"], 0)
        self.assertEqual(result["overall"]["annotated_count"], 0)
        self.assertIsNone(result["overall"]["accuracy"])
        self.assertIsNone(result["overall"]["pair_accuracy"])

    def test_same_seed_and_different_input_order_have_same_blinding(self):
        rows = [record(q, i, 20 + i * 10) for q in ("q1", "q2", "q3") for i in range(4)]
        self.export(rows, name="one", seed=7, max_pairs=8)
        self.export(list(reversed(rows)), name="two", seed=7, max_pairs=8)
        for filename in ("blind_review.md", "labels.template.jsonl", "private_key.json"):
            self.assertEqual((self.root / "one" / filename).read_bytes(), (self.root / "two" / filename).read_bytes())

    def test_query_split_never_splits_pairs_from_same_query(self):
        rows = [record(q, i) for q in ("q1", "q2", "q3") for i in range(3)]
        metadata = self.export(rows, max_pairs=8)
        key = json.loads(self.key().read_text(encoding="utf-8"))
        self.assertFalse(metadata["only_pilot"])
        self.assertEqual(set(key["query_split"].values()), {"dev", "validation"})
        for qid in key["query_split"]:
            self.assertEqual(len({p["split"] for p in key["pairs"] if p["query_id"] == qid}), 1)

    def test_single_query_remains_pilot_even_with_many_pairs(self):
        metadata = self.export([record(index=i) for i in range(12)], max_pairs=40)
        self.assertEqual(metadata["pair_count"], 40)
        self.assertEqual(metadata["query_count"], 1)
        self.assertTrue(metadata["only_pilot"])
        result = evaluate_labels(self.key(), self.root / "review/labels.template.jsonl")
        self.assertFalse(result["generalization_established"])
        self.assertEqual(result["splits"]["validation"]["pair_count"], 0)

    def test_paraphrases_share_split_and_one_independent_statistical_unit(self):
        rows = [record(q, i, 50) for q in ("original", "paraphrase", "different") for i in range(2)]
        for row in rows:
            row["query_group"] = "source-one" if row["query_id"] != "different" else "source-two"
        metadata = self.export(rows, max_pairs=3)
        key = json.loads(self.key().read_text(encoding="utf-8"))
        self.assertEqual(metadata["query_count"], 3)
        self.assertEqual(metadata["independent_query_count"], 2)
        self.assertEqual(key["query_split"]["original"], key["query_split"]["paraphrase"])
        labels = [{"pair_id": p["pair_id"], "winner": "a" if p["query_id"] == "original" else "tie"}
                  for p in key["pairs"]]
        result = evaluate_labels(self.key(), self.labels(labels))
        self.assertEqual(result["overall"]["accuracy"], 0.75)
        self.assertAlmostEqual(result["overall"]["pair_accuracy"], 2 / 3)
        pilot = self.export([r for r in rows if r["query_id"] != "different"], name="aliases_only")
        self.assertEqual(pilot["query_count"], 2)
        self.assertEqual(pilot["independent_query_count"], 1)
        self.assertTrue(pilot["only_pilot"])

    def test_model_tie_against_human_winner_counts_as_error(self):
        self.export([record(index=0, score=50), record(index=1, score=52)])
        result = evaluate_labels(self.key(), self.labels([{"pair_id": "P0001", "winner": "a", "reason": "Test fixture"}]))
        self.assertEqual(result["overall"]["accuracy"], 0)
        self.assertEqual(result["overall"]["scorer_tie_against_human_winner_count"], 1)

    def test_human_tie_is_evaluated_separately_and_uncertain_is_excluded(self):
        self.export([record(index=i, score=50) for i in range(3)], max_pairs=3)
        labels = self.labels([{"pair_id": "P0001", "winner": "tie"},
                              {"pair_id": "P0002", "winner": "uncertain"},
                              {"pair_id": "P0003", "winner": None}])
        result = evaluate_labels(self.key(), labels)["overall"]
        self.assertEqual(result["annotated_count"], 2)
        self.assertEqual(result["uncertain_count"], 1)
        self.assertEqual(result["unlabeled_count"], 1)
        self.assertEqual(result["accuracy"], 1)
        self.assertEqual(result["human_tie_pair_accuracy"], 1)
        self.assertEqual(result["comparable_count"], 1)

    def test_missing_score_does_not_receive_a_fabricated_prediction(self):
        self.export([record(index=0, score=None), record(index=1, score=80)])
        result = evaluate_labels(self.key(), self.labels([{"pair_id": "P0001", "winner": "a"}]))["overall"]
        self.assertEqual(result["missing_prediction_count"], 1)
        self.assertEqual(result["prediction_coverage_on_usable_labels"], 0)
        self.assertIsNone(result["accuracy"])

    def test_duplicate_unknown_and_invalid_labels_are_rejected(self):
        self.export()
        bad_sets = [
            [{"pair_id": "P0001", "winner": "a"}, {"pair_id": "P0001", "winner": "b"}],
            [{"pair_id": "not-real", "winner": "a"}],
            [{"pair_id": "P0001", "winner": "unknown"}],
            [{"pair_id": "P0001", "winner": 1}],
            [{"pair_id": "P0001"}],
        ]
        for i, labels in enumerate(bad_sets):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                evaluate_labels(self.key(), self.labels(labels, filename=f"bad{i}.jsonl"))

    def test_repeated_runs_are_deduplicated(self):
        first = record(index=0)
        repeated = deepcopy(first)
        repeated["method"] = "another-method-same-plan"
        metadata = self.export([first, repeated, record(index=1)])
        self.assertEqual(metadata["unique_plan_count"], 2)
        self.assertEqual(metadata["pair_count"], 1)

    def test_profiles_do_not_cross_and_existing_output_is_not_overwritten(self):
        metadata = self.export([record(index=0, profile="rules"), record(index=1, profile="full")])
        self.assertEqual(metadata["pair_count"], 0)
        with self.assertRaises(FileExistsError):
            self.export()

    def test_accuracy_uses_queries_as_independent_units(self):
        rows = [record("many", i, 50) for i in range(3)] + [record("one", i, 50) for i in range(2)]
        self.export(rows, max_pairs=4)
        key = json.loads(self.key().read_text(encoding="utf-8"))
        labels = [{"pair_id": p["pair_id"], "winner": "tie" if p["query_id"] == "many" else "a"}
                  for p in key["pairs"]]
        result = evaluate_labels(self.key(), self.labels(labels))["overall"]
        self.assertEqual(result["pair_accuracy"], 0.75)
        self.assertEqual(result["accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
