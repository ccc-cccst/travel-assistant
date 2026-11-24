"""Regression checks for evidence admission, comparison and end-to-end offline IO."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.evaluation.adapters import import_manifest, publication_state, requirement_for
from experiments.evaluation.common import PROJECT, plan_fingerprint, read_json, write_json
from experiments.evaluation.comparison import aggregate, make_pairs, outcome
from experiments.evaluation.recorder import RunRecorder


def plan(name="A"):
    return {"people_number": 1, "start_city": "A", "target_city": "B",
            "itinerary": [{"day": 1, "activities": [{"type": "attraction", "position": name}]}]}


def run(method="baseline", **changes):
    row = {"run_id": method, "method": method, "query_id": "q", "query_sha256": "qhash",
           "resource_sha256": "rhash", "requirements_sha256": "phash", "scope": "frozen_search",
           "protocol": "published_v1", "budget": {"kind": "wall_seconds", "limit": 5},
           "profile": "fast_rules_v0", "evaluation_identity": "v1", "activity_policy": "rules",
           "repetition": 1, "order_id": "same", "seed": 1, "executed": True,
           "validity": "valid", "quality_score": 80, "preference_score": None,
           "composite_score": 80, "measurement_status": "known", "planning_seconds": 5}
    row.update(changes)
    return row


class EvidenceTests(unittest.TestCase):
    def record(self, publications, terminal=True):
        events = [{"event": "run_started", "at_seconds": 0}]
        for stamp, value in publications:
            events.append({"event": "incumbent_published", "at_seconds": stamp,
                           "fingerprint": plan_fingerprint(value), "plan": value})
        if terminal:
            events.append({"event": "run_finished", "at_seconds": 9})
        return run(events=events, selected_plan=plan("late"), candidates=[])

    def test_later_better_result_cannot_backfill(self):
        state = publication_state(self.record([(2, plan("early")), (6, plan("late"))]))
        self.assertEqual(state["selected_plan"], plan("early"))
        self.assertEqual(state["measurement_status"], "known")

    def test_only_late_plan_is_no_result(self):
        self.assertEqual(publication_state(self.record([(6, plan())]))["selected_plan"], {})

    def test_missing_terminal_is_unknown(self):
        state = publication_state(self.record([(2, plan())], terminal=False))
        self.assertEqual(state["measurement_status"], "unknown")

    def test_bad_fingerprint_is_unknown(self):
        data = self.record([(2, plan())])
        data["events"][1]["fingerprint"] = "wrong"
        self.assertEqual(publication_state(data)["measurement_status"], "unknown")

    def test_late_final_is_distinct_from_first_result(self):
        data = self.record([(2, plan())], terminal=False)
        data["events"] += [{"event": "final_published", "at_seconds": 6, "fingerprint": plan_fingerprint(plan())}, {"event": "run_finished", "at_seconds": 7}]
        state = publication_state(data)
        self.assertFalse(state["first_result_late"])
        self.assertTrue(state["final_delivery_late"])

    def test_legacy_has_no_budget_claim(self):
        state = publication_state(run(protocol="legacy_posthoc", selected_plan=plan()))
        self.assertEqual(state["measurement_status"], "not_applicable")

    def test_changed_query_invalidates_requirement_review(self):
        query = read_json(PROJECT / "experiments/c_only/queries/easy_1.json")
        self.assertTrue(requirement_for("easy_1", query)["reviewed"])
        query["nature_language"] += " changed"
        self.assertFalse(requirement_for("easy_1", query)["reviewed"])

    def test_recorder_can_be_imported_without_posthoc_choice(self):
        with tempfile.TemporaryDirectory(dir=PROJECT / "artifacts") as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to((PROJECT / "artifacts").resolve()))
            now = [0.0]
            rec = RunRecorder(Path(temp) / "test", query={"uid": "q"}, method="baseline",
                              case_id="test", budget_seconds=5, clock=lambda: now[0])
            now[0] = 2
            rec.publish(plan("early"))
            now[0] = 6
            rec.publish(plan("late"))
            now[0] = 7
            rec.finish("wall_limit_reached")
            imported = import_manifest(rec.directory / "manifest.json")[0]
            self.assertEqual(publication_state(imported)["selected_plan"], plan("early"))
            with self.assertRaises(RuntimeError):
                rec.publish(plan())

    def test_backtracking_cannot_change_published_snapshot(self):
        with tempfile.TemporaryDirectory(dir=PROJECT / "artifacts") as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to((PROJECT / "artifacts").resolve()))
            recorder = RunRecorder(Path(temp) / "test", query={"uid": "q"}, method="baseline",
                                   case_id="test", budget_seconds=5)
            mutable = plan("original")
            recorder.publish(mutable)
            mutable["itinerary"][0]["activities"].clear()
            recorder.publish(plan("next"))
            recorder.finish()
            candidates = read_json(recorder.directory / "candidates.json")
            self.assertEqual(candidates[0]["plan"], plan("original"))

    def test_post_terminal_publication_is_unknown(self):
        data = self.record([], terminal=False)
        data["events"] += [{"event": "run_finished", "at_seconds": 1},
                           {"event": "incumbent_published", "at_seconds": 2,
                            "fingerprint": plan_fingerprint(plan()), "plan": plan()}]
        state = publication_state(data)
        self.assertEqual(state["measurement_status"], "unknown")
        self.assertEqual(state["selected_plan"], {})

    def test_fake_final_does_not_hide_late_delivery(self):
        data = self.record([(2, plan())], terminal=False)
        data["events"] += [{"event": "final_published", "at_seconds": 3, "fingerprint": "wrong"},
                           {"event": "run_finished", "at_seconds": 4}]
        self.assertEqual(publication_state(data)["measurement_status"], "unknown")


class ComparisonTests(unittest.TestCase):
    def test_invalid_cannot_win_with_high_score(self):
        self.assertEqual(outcome(run(validity="invalid", composite_score=100), run())["outcome"], "loss")

    def test_unknown_is_not_failure(self):
        self.assertEqual(outcome(run(validity="unknown"), run(validity="no_result"))["outcome"], "unresolved")

    def test_both_fail_is_not_tie(self):
        self.assertEqual(outcome(run(validity="invalid"), run(validity="no_result"))["outcome"], "both_failed")

    def test_incomplete_preference_does_not_fall_back(self):
        self.assertEqual(outcome(run(composite_score=None), run())["outcome"], "unresolved")

    def test_known_quality_difference_survives_missing_preference(self):
        pair = outcome(run(composite_score=None, quality_score=90), run())
        self.assertEqual(pair["outcome"], "unresolved")
        self.assertEqual(pair["quality_difference"], 10)

    def test_tie_margin_is_inclusive(self):
        self.assertEqual(outcome(run(composite_score=83), run())["outcome"], "tie")

    def test_different_budget_cannot_pair(self):
        pairs = make_pairs([run(), run("new", budget={"kind": "wall_seconds", "limit": 15})], "baseline")
        self.assertEqual(pairs[0]["status"], "unmatched")

    def test_legacy_not_in_strict_denominator(self):
        rows = [run(protocol="legacy_posthoc"), run("new", protocol="legacy_posthoc", composite_score=90)]
        summary = aggregate(rows, make_pairs(rows, "baseline"))
        self.assertTrue(all(s["feasible_by_budget"] is None and s["strict_budget_win_rate"] is None for s in summary))

    def test_repeated_query_not_overweighted(self):
        rows = [run(run_id=f"q1-{i}", query_id="q1", repetition=i, feasible_by_budget=True) for i in range(9)]
        rows += [run(run_id="q2", query_id="q2", feasible_by_budget=False)]
        self.assertEqual(aggregate(rows, [])[0]["feasible_by_budget"], .5)

    def test_duplicate_matching_run_rejected(self):
        with self.assertRaises(ValueError):
            make_pairs([run(), run()], "baseline")


class PipelineTests(unittest.TestCase):
    def test_real_plan_publication_cutoff_integrated(self):
        from experiments.evaluation.run import evaluate
        selected = PROJECT / "artifacts/prefix_probe/EXP001_easy_1/rules_n200_c0/rank_fixed/selected.json"
        if not selected.exists():
            self.skipTest("local experiment artifacts absent")
        actual_plan = read_json(selected)
        actual_query = read_json(PROJECT / "experiments/c_only/queries/easy_1.json")
        with tempfile.TemporaryDirectory(dir=PROJECT / "artifacts") as temp:
            root = Path(temp).resolve()
            self.assertTrue(root.is_relative_to((PROJECT / "artifacts").resolve()))
            entries = []
            for method, stamp in (("baseline", 2), ("late", 6)):
                now = [0.0]
                rec = RunRecorder(root/method, query=actual_query, method=method, case_id="easy_1",
                                  budget_seconds=5, requirements={"reviewed": True, "items": []}, clock=lambda: now[0])
                now[0] = stamp
                rec.publish(actual_plan)
                now[0] += .1
                rec.finish("wall_limit_reached" if stamp > 5 else "search_exhausted")
                entries.extend(read_json(rec.directory/"manifest.json")["runs"])
            write_json(root/"combined.json", {"schema_version":1,"runs":entries})
            result = evaluate(root/"combined.json", root/"report", baseline="baseline")
            bymethod = {r["method"]: r for r in result["summaries"]}
            self.assertEqual(bymethod["baseline"]["feasible_by_budget"], 1)
            self.assertEqual(bymethod["late"]["feasible_by_budget"], 0)
            self.assertEqual(bymethod["late"]["comparison_counts"], {"loss": 1})

    def test_legacy_full_unavailable_does_not_make_up_score(self):
        from experiments.evaluation.run import evaluate
        source = PROJECT / "artifacts/prefix_probe/EXP001_easy_1/rules_n200_c0"
        if not source.exists():
            self.skipTest("local experiment artifacts absent")
        with tempfile.TemporaryDirectory(dir=PROJECT / "artifacts") as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to((PROJECT / "artifacts").resolve()))
            output = Path(temp) / "report"
            result = evaluate(source, output, config={"profile": "full_v0"})
            self.assertEqual(result["judge_request_count"], 0)
            self.assertEqual(result["run_count"], 4)
            self.assertTrue(all(row["quality_mean"] is None for row in result["summaries"]))
            self.assertIn("历史回放", (output/"REPORT.md").read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                evaluate(source, output)


if __name__ == "__main__":
    unittest.main()
