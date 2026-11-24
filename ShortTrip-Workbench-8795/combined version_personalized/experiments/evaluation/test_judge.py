"""Semantic judge contract tests; all HTTP is mocked, no model/server required."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib import error, request

from experiments.evaluation import judge


PLAN = {"people_number": 1, "start_city": "A", "target_city": "B", "method": "secret-planner",
        "itinerary": [{"day": 1, "activities": [{"type": "attraction", "position": "博物馆",
                                                  "start_time": "09:00", "end_time": "11:00"}]}]}
QUERY = {"text": "在B市轻松逛博物馆", "method": "identity-must-not-reach-model"}
REQUIREMENTS = {"reviewed": True, "items": [{"id": "museum", "method": "semantic", "kind": "soft",
                                             "text": "喜欢博物馆", "source": "用户原文"}]}
CONFIG = {"mode": "enabled", "model": "test-local", "model_version": "weights-sha256-123",
          "base_url": "http://127.0.0.1:18080/v1", "api_key_env": "SHORTTRIP_TEST_JUDGE_KEY"}
RESOURCES = {"snapshot_sha256": "resource-1", "attractions": {"博物馆": {"name": "博物馆", "type": "博物馆"}},
             "hotels": {}, "restaurants": {}}


def metric(rating=3, **overrides):
    return {"status": "known", "rating": rating, "evidence": ["d1-a1"], "reason": "博物馆安排充分", **overrides}


def payload():
    return {"experience": metric(), "preferences": [{"id": "museum", **metric(4)}]}


def response(value=None, *, content=None):
    result = {"choices": [{"message": {"content": content if content is not None else json.dumps(value or payload())}}],
              "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}}
    return io.BytesIO(json.dumps(result).encode())


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        clean_env = {key: value for key, value in os.environ.items()
                     if not key.startswith("SHORTTRIP_JUDGE_") and key != "OPENAI_API_KEY"}
        self.env = patch.dict(os.environ, clean_env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def call(self, **kwargs):
        args = {"requirements": REQUIREMENTS, "resources": RESOURCES,
                "config": CONFIG, "cache_dir": self.cache, "allow_requests": True}
        args.update(kwargs)
        return judge.judge_plan(PLAN, QUERY, **args)

    def test_defaults_and_permission_do_not_open_http(self):
        with patch.object(request, "build_opener") as network:
            default = judge.judge_plan(PLAN, QUERY)
            self.assertEqual(default["status"], "unknown")
            self.assertEqual(default["request_count"], 0)
            for cfg, allowed in ((CONFIG, False), ({**CONFIG, "mode": "cache_only"}, True),
                                 ({**CONFIG, "mode": "disabled"}, True), (CONFIG, "true")):
                result = self.call(config=cfg, allow_requests=allowed)
                self.assertEqual(result["request_count"], 0)
            network.assert_not_called()
        absent_version = self.call(config={**CONFIG, "model_version": None})
        self.assertEqual(absent_version["reason"], "missing_model_configuration")
        self.assertEqual(absent_version["request_count"], 0)

    def test_http_contract_cache_and_method_blinding(self):
        with patch.dict(os.environ, {"SHORTTRIP_TEST_JUDGE_KEY": "private-key-do-not-log"}):
            with patch.object(request, "build_opener") as network:
                network.return_value.open.return_value = response()
                original = copy.deepcopy(PLAN)
                first = self.call()
                req = network.return_value.open.call_args.args[0]
                self.assertEqual(req.full_url, "http://127.0.0.1:18080/v1/chat/completions")
                self.assertEqual(req.get_header("Authorization"), "Bearer private-key-do-not-log")
                body = json.loads(req.data)
                self.assertEqual(body["model"], "test-local")
                self.assertFalse(body["stream"])
                user = json.loads(body["messages"][1]["content"])
                self.assertNotIn("method", user["plan"])
                self.assertNotIn("method", user["query"])
                self.assertEqual(user["semantic_requirements"][0]["method"], "semantic")
                self.assertEqual(user["plan"]["itinerary"][0]["activities"][0]["activity_id"], "d1-a1")
                self.assertEqual(PLAN, original)
                changed = copy.deepcopy(PLAN)
                changed["method"] = "another-method"
                second = judge.judge_plan(changed, QUERY, requirements=REQUIREMENTS,
                    resources=RESOURCES, config={**CONFIG, "mode": "cache_only"}, cache_dir=self.cache)
                self.assertTrue(second["cache_hit"])
                self.assertEqual(first["cache_key"], second["cache_key"])
                self.assertEqual(first["status"], "known")
                self.assertEqual(second["status"], "known")
                self.assertEqual(second["request_count"], 0)
                self.assertEqual(second["usage"], {})
                self.assertEqual(second["historical_usage"]["total_tokens"], 19)
                network.return_value.open.assert_called_once()
            record = json.loads(next(self.cache.glob("*.json")).read_text(encoding="utf-8"))
            self.assertIn("raw_response", record["attempts"][0])
            self.assertNotIn("private-key-do-not-log", json.dumps(record))

    def test_semantic_changes_invalidate_cache(self):
        with patch.object(request, "build_opener") as network:
            network.return_value.open.return_value = response()
            first = self.call()
            changed_requirements = copy.deepcopy(REQUIREMENTS)
            changed_requirements["items"][0]["text"] = "更喜欢艺术博物馆"
            changes = [dict(config={**CONFIG, "model_version": "new-weights"}),
                       dict(config={**CONFIG, "params": {"seed": 7}}),
                       dict(config={**CONFIG, "evaluation_version": "full_v1"}),
                       dict(resources={"snapshot_digest": "resource-2"}),
                       dict(requirements=changed_requirements)]
            for change in changes:
                other = self.call(allow_requests=False, **change)
                self.assertNotEqual(first["cache_key"], other["cache_key"])
                self.assertFalse(other["cache_hit"])
                self.assertEqual(other["request_count"], 0)
            network.return_value.open.assert_called_once()

    def test_bad_json_retries_once_and_preserves_both_attempts(self):
        with patch.object(request, "build_opener") as network:
            network.return_value.open.side_effect = [response(content="not JSON"), response()]
            result = self.call()
            self.assertEqual(result["status"], "known")
            self.assertEqual(result["request_count"], 2)
            self.assertEqual(result["usage"]["total_tokens"], 38)
            self.assertEqual(len(result["attempts"]), 2)
            self.assertIn("invalid_response_format", result["attempts"][0]["validation_errors"])

    def test_missing_required_metrics_stay_unknown_and_cache_does_not_retry(self):
        malformed = {"preferences": []}
        with patch.object(request, "build_opener") as network:
            network.return_value.open.side_effect = [response(malformed), response(malformed)]
            result = self.call()
            self.assertEqual(result["status"], "unknown")
            self.assertIsNone(result["experience"]["rating"])
            self.assertIsNone(result["preferences"][0]["rating"])
            self.assertEqual(result["request_count"], 2)
            cached = self.call()
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(cached["request_count"], 0)
            self.assertEqual(cached["status"], "unknown")
            self.assertEqual(network.return_value.open.call_count, 2)

    def test_malformed_response_envelope_becomes_unknown_after_two_attempts(self):
        with patch.object(request, "build_opener") as network:
            network.return_value.open.side_effect = [io.BytesIO(b'[]'), io.BytesIO(b'[]')]
            result = self.call()
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["request_count"], 2)
            cached = self.call(allow_requests=False)
            self.assertEqual(cached["status"], "unknown")
            self.assertIsNone(cached["experience"]["rating"])

    def test_order_and_timing_remain_semantic_and_existing_id_is_preserved(self):
        original = copy.deepcopy(PLAN)
        original["itinerary"][0]["activities"][0]["activity_id"] = "source-id"
        result = judge.judge_plan(original, QUERY, config=CONFIG)
        changed = copy.deepcopy(original)
        changed["itinerary"][0]["activities"][0]["end_time"] = "10:00"
        later = judge.judge_plan(changed, QUERY, config=CONFIG)
        self.assertNotEqual(result["cache_key"], later["cache_key"])
        self.assertEqual(result["activity_ids"], ["source-id"])

    def test_known_zero_and_unknown_numeric_values_are_distinct(self):
        value = payload()
        value["experience"] = metric(0)
        value["preferences"][0] = {"id": "museum", **metric(4, status="unknown", reason="缺资料")}
        with patch.object(request, "build_opener") as network:
            network.return_value.open.return_value = response(value)
            result = self.call()
        self.assertEqual(result["experience"]["rating"], 0)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["preferences"][0]["rating"])
        self.assertEqual(result["request_count"], 1)

    def test_nonexistent_evidence_and_boolean_ratings_cannot_score(self):
        for bad in (metric(evidence=["invented"]), metric(True), metric(5), metric(evidence=[])):
            with self.subTest(metric=bad), patch.object(request, "build_opener") as network:
                value = payload()
                value["experience"] = bad
                network.return_value.open.side_effect = [response(value), response(value)]
                result = self.call(cache_dir=None)
                self.assertEqual(result["experience"]["status"], "unknown")
                self.assertIsNone(result["experience"]["rating"])

    def test_unreviewed_preferences_are_not_sent_or_trusted(self):
        requirements = {**REQUIREMENTS, "reviewed": False}
        value = {"experience": metric(), "preferences": []}
        with patch.object(request, "build_opener") as network:
            network.return_value.open.return_value = response(value)
            result = self.call(requirements=requirements)
            body = json.loads(network.return_value.open.call_args.args[0].data)
            self.assertEqual(json.loads(body["messages"][1]["content"])["semantic_requirements"], [])
        self.assertEqual(result["experience"]["status"], "known")
        self.assertEqual(result["preferences_status"], "unknown")
        self.assertIsNone(result["preferences"][0]["rating"])

    def test_experience_not_applicable_does_not_block_preferences(self):
        value = {"preferences": [{"id": "museum", **metric(4)}]}
        with patch.object(request, "build_opener") as network:
            network.return_value.open.return_value = response(value)
            result = self.call(config={**CONFIG, "experience_applicable": False})
        self.assertEqual(result["status"], "known")
        self.assertEqual(result["experience"]["status"], "not_applicable")
        self.assertIsNone(result["experience"]["rating"])

    def test_environment_configuration_and_no_inline_key(self):
        with patch.dict(os.environ, {"SHORTTRIP_JUDGE_MODEL": "env-model", "SHORTTRIP_JUDGE_MODEL_VERSION": "env-revision",
                                    "SHORTTRIP_JUDGE_BASE_URL": "http://localhost:1234/v1", "SHORTTRIP_JUDGE_PARAMS_JSON": '{"seed":5}'}):
            result = self.call(config=None)
            self.assertEqual(result["model"], "env-model")
            self.assertEqual(result["model_version"], "env-revision")
            self.assertEqual(result["request_count"], 0)
        with self.assertRaises(ValueError):
            self.call(config={**CONFIG, "api_key": "never-inline"})

    def test_resolve_config_pins_environment_version_without_reading_secret(self):
        with patch.dict(os.environ, {"SHORTTRIP_JUDGE_MODEL_VERSION": "exact-weights-revision-a",
                                    "SHORTTRIP_TEST_JUDGE_KEY": "must-not-be-read-or-serialized"}):
            real_get = os.environ.get
            def checked_get(name, *args):
                if name == "SHORTTRIP_TEST_JUDGE_KEY":
                    self.fail("resolve_config must not read the API key value")
                return real_get(name, *args)
            with patch.object(os.environ, "get", side_effect=checked_get), patch.object(request, "build_opener") as network:
                config = {**CONFIG, "model_version": None}
                first = judge.resolve_config(config)
                self.assertEqual(first["model_version"], "exact-weights-revision-a")
                self.assertEqual(first["api_key_env"], "SHORTTRIP_TEST_JUDGE_KEY")
                self.assertNotIn("must-not-be-read-or-serialized", json.dumps(first))
                os.environ["SHORTTRIP_JUDGE_MODEL_VERSION"] = "exact-weights-revision-b"
                second = judge.resolve_config(config)
                self.assertNotEqual(first, second)
                self.assertEqual(judge.resolve_config(first), first)
                network.assert_not_called()

    def test_unsafe_endpoint_rejected_without_network(self):
        with patch.object(request, "build_opener") as network:
            for endpoint in ("file:///etc/passwd", "ftp://example.com", "https://user:secret@example.com/v1",
                             "http://localhost/v1?token=x", "http://localhost/v1#fragment", "http://localhost:bad/v1"):
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    self.call(config={**CONFIG, "base_url": endpoint})
            network.assert_not_called()

    def test_redirect_blocked_and_no_credential_forwarding(self):
        handler = judge._NoRedirect()
        req = request.Request(CONFIG["base_url"], headers={"Authorization": "Bearer secret"})
        self.assertIsNone(handler.redirect_request(req, None, 302, "Found", {}, "https://other.invalid"))
        with patch.object(request, "build_opener") as network:
            network.return_value.open.side_effect = error.HTTPError(CONFIG["base_url"], 302, "Found", {}, None)
            result = self.call()
            self.assertIsInstance(network.call_args.args[0], judge._NoRedirect)
            self.assertEqual(result["reason"], "redirect_rejected")
            self.assertEqual(result["request_count"], 1)
            network.return_value.open.assert_called_once()

    def test_corrupt_cache_is_not_overwritten_or_used(self):
        with patch.object(request, "build_opener") as network:
            network.return_value.open.return_value = response()
            first = self.call()
            path = self.cache / (first["cache_key"] + ".json")
            path.write_text('{"unexpected":"corrupt"}', encoding="utf-8")
            result = self.call()
            self.assertEqual(result["reason"], "invalid_cache")
            self.assertEqual(path.read_text(encoding="utf-8"), '{"unexpected":"corrupt"}')
            network.return_value.open.assert_called_once()

    def test_resource_subset_keeps_full_digest_and_missing_is_unknown(self):
        resources = copy.deepcopy(RESOURCES)
        resources["attractions"]["未访问公园"] = {"name": "未访问公园", "price": 15}
        with patch.object(request, "build_opener") as network:
            network.return_value.open.return_value = response()
            result = self.call(resources=resources)
            body = json.loads(network.return_value.open.call_args.args[0].data)
            sent = json.loads(body["messages"][1]["content"])["resources"]
            self.assertEqual(set(sent["attractions"]), {"博物馆"})
            self.assertEqual(result["resource_scope"]["source_counts"]["attractions"], 2)
            self.assertEqual(sent["snapshot_metadata"]["snapshot_sha256"], "resource-1")
            resources["attractions"]["未访问公园"]["price"] = 20
            changed = self.call(resources=resources, allow_requests=False)
            self.assertNotEqual(result["cache_key"], changed["cache_key"])
            missing = self.call(resources={"attractions": {}})
            self.assertEqual(missing["reason"], "missing_referenced_resources")
            self.assertEqual(missing["request_count"], 0)
            self.assertIsNone(missing["experience"]["rating"])
            network.return_value.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
