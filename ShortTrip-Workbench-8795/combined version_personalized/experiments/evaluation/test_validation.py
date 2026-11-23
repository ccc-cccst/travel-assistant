"""Tests of legality vs evaluator failure, including a real EXP001 itinerary."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from experiments.evaluation import validation


class SchemaValidationTests(unittest.TestCase):
    def test_malformed_plan_stops_before_local_database_loading(self):
        with patch.object(validation, "_commonsense_backend") as backend:
            result = validation.validate_plan({"itinerary": []}, {})
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["checks"][0]["name"], "schema")
        self.assertEqual(result["errors"], [])
        backend.assert_not_called()

    def test_schema_dependency_failure_is_unknown(self):
        with patch.object(validation, "_schema_validator", side_effect=ImportError("missing jsonschema")):
            result = validation.validate_plan({}, {})
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["violations"], [])

    def test_unsupported_protocol_cannot_silently_skip_c_audit(self):
        plan = {"people_number": 1, "start_city": "南京", "target_city": "重庆", "itinerary": []}
        with patch.object(validation, "_commonsense_backend") as backend:
            result = validation.validate_plan(plan, {}, config={"protocol": "multi_destination"})
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["errors"][0]["details"]["reason"], "unsupported_protocol")
        backend.assert_not_called()


class RealPlanValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = validation.PROJECT / "artifacts/prefix_probe/EXP001_easy_1/rules_n200_c0/rank_fixed/selected.json"
        query = validation.PROJECT / "experiments/c_only/queries/easy_1.json"
        if not selected.exists():
            raise unittest.SkipTest("Real EXP001 artifact not available; replay integration tests require it")
        cls.fixture_plan = json.loads(selected.read_text(encoding="utf-8"))
        cls.fixture_query = json.loads(query.read_text(encoding="utf-8"))

    def setUp(self):
        self.plan = deepcopy(self.fixture_plan)
        self.query = deepcopy(self.fixture_query)

    def test_real_plan_passes_all_three_independent_check_families(self):
        result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "valid", result["errors"] + result["violations"])
        names = {c["name"] for c in result["checks"]}
        self.assertTrue(set(validation.COMMONSENSE_NAMES) <= names)
        self.assertIn("C_audit", names)
        self.assertIn("dataset_hard/4", names)
        self.assertEqual(self.plan, self.fixture_plan)
        self.assertEqual(self.query, self.fixture_query)

    def test_no_hard_constraints_is_not_applicable(self):
        self.query["hard_logic_py"] = []
        result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "valid", result)
        hard = next(c for c in result["checks"] if c["name"] == "dataset_hard")
        self.assertEqual(hard["status"], "not_applicable")

    def test_missing_hard_constraints_are_distinct_from_explicit_empty(self):
        del self.query["hard_logic_py"]
        result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["violations"], [])

    def test_dsl_error_is_unknown_and_not_a_constraint_violation(self):
        self.query["hard_logic_py"] = ["result = unavailable_function(plan)"]
        result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["errors"][0]["details"]["reason"], "dsl_evaluation_error")

    def test_explicit_false_constraint_is_invalid(self):
        self.query["hard_logic_py"] = ["result = False"]
        result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["violations"][0]["name"], "dataset_hard/0")

    def test_unavailable_reference_database_is_unknown(self):
        with patch.object(validation, "_commonsense_backend", side_effect=FileNotFoundError("missing snapshot")):
            result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["errors"][0]["name"], "commonsense_resources")

    def test_invalid_clock_rejected_without_calling_database(self):
        self.plan["itinerary"][0]["activities"][0]["start_time"] = "27:15"
        with patch.object(validation, "_commonsense_backend") as backend:
            result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["violations"][0]["name"], "clock_bounds")
        backend.assert_not_called()

    def test_nan_cost_cannot_bypass_numeric_comparisons(self):
        self.plan["itinerary"][0]["activities"][0]["cost"] = float("nan")
        with patch.object(validation, "_commonsense_backend") as backend:
            result = validation.validate_plan(self.plan, self.query)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["violations"][0]["name"], "finite_numbers")
        backend.assert_not_called()

    def test_unimplemented_hard_requirement_is_unknown(self):
        reqs = {"reviewed": True, "items": [{"id": "appointment", "kind": "hard", "method": "semantic"}]}
        result = validation.validate_plan(self.plan, self.query, requirements=reqs)
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["errors"][0]["name"], "requirement/appointment")

    def test_reviewed_hard_requirement_is_checked(self):
        reqs = {"reviewed": True, "items": [{"id": "budget", "kind": "hard", "method": "dsl", "expression": "result = False"}]}
        result = validation.validate_plan(self.plan, self.query, requirements=reqs)
        self.assertEqual(result["status"], "invalid", result)
        self.assertEqual(result["violations"][0]["name"], "requirement/budget")

    def test_works_from_an_unrelated_working_directory(self):
        old_cwd = Path.cwd()
        try:
            os.chdir(validation.PROJECT.parent)
            result = validation.validate_plan(self.plan, self.query)
        finally:
            os.chdir(old_cwd)
        self.assertEqual(result["status"], "valid", result)


if __name__ == "__main__":
    unittest.main()
