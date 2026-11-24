import unittest

from experiments.timing_probe.activity_granularity import HalfDayActivityPolicy, experiment_agent, preference_order


class ActivityGranularityTests(unittest.TestCase):
    def setUp(self):
        self.query = {"days": 2, "nature_language": "两天轻松逛"}
        self.options = {"back_transport": {"From": "上海站", "BeginTime": "16:00"}}

    def test_each_half_day_uses_one_model_order(self):
        responses = []

        def model(messages, **kwargs):
            responses.append((messages, kwargs))
            return '{"priority":["lunch","attraction","hotel"]}'

        policy = HalfDayActivityPolicy()
        self.assertEqual(policy.decide(model, ["attraction", "lunch"], self.query, 0, "11:30", "酒店", self.options), "lunch")
        self.assertEqual(policy.decide(model, ["attraction", "hotel"], self.query, 0, "11:50", "景点", self.options), "attraction")
        self.assertEqual(policy.decide(model, ["attraction", "hotel"], self.query, 0, "13:50", "景点", self.options), "attraction")
        self.assertEqual(len(responses), 2)
        self.assertEqual(len(policy.events), 2)

    def test_invalid_response_preserves_remaining_candidates(self):
        self.assertEqual(preference_order('{"priority":["flight","lunch","lunch",38]}'), ("lunch",))
        self.assertEqual(preference_order("broken"), ())

        class Agent:
            def __init__(self):
                self.backbone_llm = lambda *_a, **_kw: "broken"
                self.query = {"days": 2, "nature_language": "普通需求"}

            def select_next_poi_type(self, candidates, plan, root, day, clock, position):
                candidates.remove("hotel")  # stand-in for the inherited meal guard
                text = self.backbone_llm([], one_line=False)
                return candidates[0] if text == "Type: attraction" else "unavailable", candidates

        instance = experiment_agent(Agent, "half_day", lambda _agent: None)()
        model = instance.backbone_llm
        chosen, remaining = instance.select_next_poi_type(["attraction", "lunch", "hotel"], [], {}, 0, "11:30", "酒店")
        self.assertEqual((chosen, remaining), ("attraction", ["attraction", "lunch"]))
        self.assertIs(instance.backbone_llm, model)
        self.assertTrue(instance.policy.events[0]["format_fallback"])

    def test_singleton_selection_requires_no_model_decision(self):
        policy = HalfDayActivityPolicy()
        self.assertEqual(policy.decide(None, ["lunch"], self.query, 0, "13:30", "餐厅", self.options), "lunch")
        self.assertEqual(policy.events, [])


if __name__ == "__main__":
    unittest.main()

