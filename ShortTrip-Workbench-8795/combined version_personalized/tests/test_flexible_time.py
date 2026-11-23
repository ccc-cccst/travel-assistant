import unittest

from chinatravel.soft_constraints.time_preferences import time_policy, timing_adjustments
from experiments.c_only.rules import attraction_slot, audit, meal_obligations


def day_plan():
    def act(kind, start, finish, **kwargs):
        return {"type": kind, "start_time": start, "end_time": finish, **kwargs}
    return {"itinerary": [{"day": 1, "activities": [
        act("train", "06:00", "07:00", duration_hours=1),
        act("breakfast", "07:30", "08:00", position="早餐店"),
        act("lunch", "14:30", "15:15", position="午餐店"),
        act("dinner", "18:00", "19:00", position="晚餐店"),
        act("train", "20:00", "21:00", duration_hours=1),
    ]}]}


class FlexibleTimeTests(unittest.TestCase):
    def test_wider_window_does_not_create_a_new_compulsory_meal_before_return(self):
        plan={"itinerary":[{"day":1,"activities":[
            {"type":"train","start_time":"07:00","end_time":"10:00","duration_hours":3},
            {"type":"train","start_time":"11:43","end_time":"15:00","duration_minutes":197},
        ]}]}
        for policy in ('strict','flexible'):
            lunch=next(o for o in meal_obligations(plan,policy) if o['day']==1 and o['kind']=='lunch')
            self.assertEqual(lunch['status'],'traffic_exempt')

    def test_late_lunch_is_feasible_and_has_a_separate_preference_warning(self):
        plan = day_plan()
        self.assertFalse(audit(plan, 1)["all_pass"])
        self.assertTrue(audit(plan, 1, "flexible")["all_pass"])
        issues = timing_adjustments(plan)["issues"]
        self.assertTrue(any(i["kind"] == "meal_time" and i["place"] == "午餐店" for i in issues))

    def test_intercity_overlap_still_fails_with_flexible_meals(self):
        plan = day_plan()
        plan["itinerary"][0]["activities"][-2].update(start_time="20:00", end_time="20:45")
        result = audit(plan, 1, "flexible")
        self.assertFalse(result["all_pass"])
        self.assertTrue(any(v["rule"] == "meal_overlaps_intercity" for v in result["violations"]))

    def test_evening_visit_can_fit_opening_hours(self):
        self.assertIsNone(attraction_slot("21:10", "09:00", "23:00", 90))
        self.assertEqual(attraction_slot("21:10", "09:00", "23:00", 90, "flexible"), ("21:10", "22:40"))
        self.assertIsNone(attraction_slot("21:10", "09:00", "21:00", 90, "flexible"))

    def test_long_wait_is_reported_and_tiny_visit_has_a_penalty(self):
        plan = {"itinerary": [{"day": 1, "activities": [
            {"type": "attraction", "position": "公园", "start_time": "09:00", "end_time": "10:00"},
            {"type": "attraction", "position": "博物馆", "start_time": "12:00", "end_time": "12:10"},
        ]}]}
        report = timing_adjustments(plan)
        self.assertEqual({i["kind"] for i in report["issues"]}, {"long_wait", "short_visit"})
        self.assertGreater(report["penalty"], 0)

    def test_unrecognized_profile_cannot_silently_relax_checks(self):
        with self.assertRaises(ValueError):
            time_policy("anything_goes")

    def test_planning_wait_and_restaurant_rules_use_the_selected_profile(self):
        from experiments.c_only.experiment_agent import ExperimentAgent
        agent = ExperimentAgent.__new__(ExperimentAgent)
        agent.group = "C"
        agent.query = {"target_city": "南京", "people_number": 1, "time_policy": "flexible"}
        agent._context = ([], {}, 0, "14:30", "酒店")
        agent.requirements = lambda root: audit(day_plan(), 1, "flexible")["obligations"]
        row = {"name": "午餐店", "opentime": "10:00", "endtime": "22:00", "price": 50}
        result = agent.add_restaurant([{"day": 1, "activities": []}], "lunch", row, 0, "14:30", [])
        self.assertEqual(result[0]["activities"][0]["end_time"], "15:15")
        self.assertTrue(agent.allow_wait("10:00", "12:00"))
        row["endtime"] = "14:45"
        with self.assertRaises(ValueError):
            agent.add_restaurant([{"day": 1, "activities": []}], "lunch", row, 0, "14:30", [])
        agent.query["time_policy"] = "strict"
        self.assertFalse(agent.allow_wait("10:00", "12:00"))

