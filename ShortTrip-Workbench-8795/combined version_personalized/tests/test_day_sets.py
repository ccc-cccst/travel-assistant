import copy
import json
import unittest

from experiments.timing_probe.day_sets import (
    DaySearchComplete, appointment_slot, daily_agent, day_capacity, geo_distance, read_proposals, read_restaurants,
)
from experiments.timing_probe.route_view import route_markdown, route_view


class DaySetTests(unittest.TestCase):
    def test_return_morning_does_not_use_midnight_as_playable_start(self):
        self.assertEqual(day_capacity("08:30", 10 * 60 + 43, 0, 0), 1)
        self.assertEqual(day_capacity("16:49", 21 * 60, 60, 0), 2)
        self.assertEqual(day_capacity("09:00", 21 * 60, 120, 0), 4)

    def test_proposals_are_unordered_and_unknown_ids_are_rejected(self):
        raw = '{"sets":[{"attractions":[2,1]},{"attractions":[1,2,2]},{"attractions":[99]},{"attractions":[true]}]}'
        self.assertEqual(read_proposals(raw, {1, 2}), [(1, 2)])
        self.assertEqual(read_proposals('{"sets": 7}', {1}), [])
        self.assertEqual(read_proposals("invalid", {1}), [])
        self.assertEqual(read_restaurants('{"restaurants":[2,2,7,true]}', {2}), (2,))

    def test_appointment_requires_arrival_by_confirmed_start(self):
        items = [{"day": 2, "name": "博物馆", "start_time": "15:00", "duration_minutes": 90}]
        self.assertEqual(appointment_slot(items, 2, "博物馆", "14:40", "09:00", "17:00", 90),
                         ("15:00", "16:30"))
        self.assertIs(appointment_slot(items, 2, "博物馆", "15:01", "09:00", "17:00", 90), False)
        self.assertIs(appointment_slot(items, 2, "博物馆", "14:40", "09:00", "16:00", 90), False)
        self.assertIsNone(appointment_slot(items, 1, "公园", "10:00", "08:00", "17:00", 90))

    def test_geographic_unknown_is_not_zero_distance(self):
        self.assertEqual(geo_distance((30, 120), (30, 120)), 0)
        self.assertEqual(geo_distance("missing", (30, 120)), float("inf"))

    def test_route_display_uses_one_witness_without_ordinary_execution_times(self):
        plan = {"itinerary": [
            {"day": 1, "activities": [
                {"type": "accommodation", "position": "酒店", "start_time": "21:00",
                 "end_time": "24:00", "transports": []}]},
            {"day": 2, "activities": [
                {"type": "attraction", "position": "博物馆", "start_time": "15:00",
                 "end_time": "16:30", "transports": [
                     {"start_time": "14:30", "end_time": "14:40", "mode": "walk"},
                     {"start_time": "14:40", "end_time": "14:55", "mode": "metro"}]},
                {"type": "train", "start": "车站", "end": "家乡站", "TrainID": "G1",
                 "start_time": "18:00", "end_time": "20:00", "transports": [
                     {"start_time": "16:30", "end_time": "17:00", "mode": "taxi"}]}]}]}
        before = copy.deepcopy(plan)
        appointments = [{"day": 2, "name": "博物馆", "start_time": "15:00"}]
        view = route_view(plan, appointments)
        self.assertEqual(plan, before)
        self.assertEqual(view["days"][1]["transfers"][0]["from"], "酒店")
        self.assertEqual(view["days"][1]["transfers"][0]["estimated_minutes"], 25)
        self.assertEqual(view["days"][1]["transfers"][1]["estimated_minutes"], 30)
        self.assertNotIn("start_time", json.dumps(view["days"][1]["stops"]))
        markdown = route_markdown(view)
        self.assertIn("15:00", markdown)
        self.assertIn("18:00", markdown)
        self.assertNotIn("16:30", markdown)

    def test_interrupted_set_restores_visiting_state(self):
        class Agent:
            def __init__(self):
                self.attractions_visiting = [7]
                self.restaurants_visiting = []
                self.food_type_visiting = []
                self.spot_type_visiting = []
                self.attraction_names_visiting = ["旧地点"]
                self.restaurant_names_visiting = []
                self.query = {"days": 2, "people_number": 1}
                self.prune_counts = {}
                self.pool = type("Pool", (), {"full": False})()
                self.innercity_transports_ranking_from_query = ["walk"]
            def prune_partial(self, *_args): return False
            def outbound_next(self, *_args): return 0, "10:00"
            def add_intercity_transport(self, activities, *_args, **_kw):
                activities.append({"type": "train", "end_time": "10:00"})
            def _deadline_check(self): pass

        instance = daily_agent(Agent, lambda _a: None)()
        root = {"go_transport": {"To": "车站"}}
        original_state = instance._visits()
        instance.build_day_options = lambda *_args, **_kw: [(1,)]
        instance._restaurant_pool = lambda *_args: []
        def fail(*_args, **_kwargs):
            instance.attractions_visiting.append(123)
            raise RuntimeError("simulated route failure")
        # Reproduce an exception within the inherited scheduler.
        Agent.dfs_poi = fail
        with self.assertRaisesRegex(RuntimeError, "simulated route failure"):
            instance._search_days(instance.query, root)
        self.assertEqual(instance._visits(), original_state)
        self.assertFalse(instance._inside_day)

    def test_trial_budget_yields_to_other_sets(self):
        class Agent:
            def __init__(self): pass
            def _deadline_check(self): pass
        instance = daily_agent(Agent, lambda _a: None)()
        instance._set_misses = instance.day_route_limit
        instance._set_cpu = 0
        with self.assertRaises(DaySearchComplete):
            instance._check_day_budget()
        self.assertEqual(instance._set_status, "trial_budget")


if __name__ == "__main__":
    unittest.main()

