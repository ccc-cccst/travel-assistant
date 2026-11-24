import unittest

from chinatravel.personalization.edit_intent import interpret_edit
from chinatravel.personalization.tripspec import ModelReply


class Facts:
    version = {"commit": "test"}

    def place_with_suggestions(self, city, name, kind):
        assert city == "上海" and kind == "restaurant"
        names = {"餐厅甲": ("餐厅甲",), "餐厅乙": ("餐厅乙静安店", "餐厅乙黄浦店")}.get(name, ())
        return {"status": "found" if len(names) == 1 else "ambiguous",
                "records": [{"name": n, "planning_catalog": True, "routable_in_snapshot": True} for n in names]}


class EditIntentTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"city": "上海", "day": 2, "snapshot": Facts.version,
                        "metrics": {"total_cost_cny": 3402.67, "city_transit_minutes": 70},
                        "activities": [
                            self.activity("lunch2", 2, "lunch", "第二天餐馆"),
                            self.activity("lunch3", 3, "lunch", "第三天餐馆"),
                            self.activity("museum", 3, "attraction", "上海博物馆", locked=True),
                        ]}

    @staticmethod
    def activity(identifier, day, kind, name, locked=False):
        return {"id": identifier, "day": day, "kind": kind, "name": name, "locked": locked,
                "start": "15:00" if locked else "12:00", "end": "16:00" if locked else "13:00",
                "has_connection": True, "arrival_note": "按规划快照预计 12:57 到达。"}

    def interpret(self, text, **draft):
        def reply(prompt, payload, **kwargs):
            assert payload["text"] == text
            assert "metrics" not in payload
            return ModelReply(draft=draft)
        return interpret_edit({**self.payload, "text": text}, Facts(), provider=reply)

    def test_explicit_day_overrides_the_open_tab_and_rejects_the_wrong_activity(self):
        text = "第三天午餐换餐厅甲"
        self.assertEqual(self.interpret(text, action="replace", activity="lunch2", place="餐厅甲")["state"], "clarify")
        result = self.interpret(text, action="replace", activity="lunch3", place="餐厅甲")
        self.assertEqual(result["operations"], [{"action": "replace", "activity": "lunch3", "place": "餐厅甲"}])

    def test_overview_asks_which_lunch_even_if_the_model_supplies_a_day(self):
        self.payload["day"] = None
        text = "帮我把午餐改到12点开始。"
        edit = {"action": "time", "activity": "lunch2", "start": "12:00", "end": "13:00"}
        result = self.interpret(text, day=2, **edit)
        self.assertEqual(result["state"], "clarify")
        self.assertRegex(result["message"], "第几天.*午餐")
        self.assertNotIn("operations", result)
        self.assertNotIn("proposal", result)
        self.payload["focus"] = "lunch2"
        self.assertEqual(self.interpret(text, **edit)["state"], "action")

    def test_open_day_and_named_activity_keep_their_actual_target(self):
        text = "把午餐改到12:15–13:15"
        edit = {"action": "time", "start": "12:15", "end": "13:15"}
        self.assertEqual(self.interpret(text, activity="lunch3", **edit)["state"], "clarify")
        self.assertEqual(self.interpret(text, activity="lunch2", **edit)["state"], "action")
        self.payload["day"] = None
        self.payload["activities"][1]["name"] = "浦江面馆"
        text = "把浦江面馆这顿午餐改到12:15–13:15"
        self.assertEqual(self.interpret(text, activity="lunch3", **edit)["state"], "action")

    def test_batch_cannot_infer_scope_for_repeated_lunches(self):
        self.payload["day"] = None
        result = self.interpret("午餐换本帮菜，下午轻松点", action="batch", day=2,
                                operations=[{"action": "meal_options", "activity": "lunch2", "cuisine": "本帮菜"}])
        self.assertEqual(result["state"], "clarify")
        self.assertNotIn("proposal", result)

    def test_new_restaurant_does_not_identify_which_lunch_to_replace(self):
        self.payload["day"] = None
        self.payload["activities"][1]["name"] = "餐厅甲"
        result = self.interpret("午餐换成餐厅甲", action="replace", activity="lunch2", place="餐厅甲")
        self.assertEqual(result["state"], "clarify")
        self.assertRegex(result["message"], "第几天.*午餐")

    def test_ambiguous_branch_yields_explicit_choices(self):
        result = self.interpret("第三天午餐换餐厅乙", action="replace", activity="lunch3", place="餐厅乙")
        self.assertEqual([op["place"] for op in result["operations"]], ["餐厅乙静安店", "餐厅乙黄浦店"])

    def test_invented_place_requests_clarification(self):
        self.assertEqual(self.interpret("第三天午餐换一家", action="replace", activity="lunch3", place="餐厅甲")["state"],
                         "clarify")

    def test_named_cuisine_goes_to_the_real_meal_recommendation(self):
        result = self.interpret("第三天午餐换一家本帮菜", action="meal_options", activity="lunch3", cuisine="本帮菜")
        self.assertEqual(result["operations"], [{"action": "meal_options", "activity": "lunch3", "cuisine": "本帮菜"}])

    def test_locked_activity_cannot_be_retimed(self):
        result = self.interpret("第三天博物馆改到16:00", action="time", activity="museum", start="16:00", end="17:00")
        self.assertEqual(result["state"], "clarify")
        self.assertIn("固定事项", result["message"])

    def test_explanation_uses_snapshot_numbers_without_operation(self):
        result = self.interpret("第三天能赶上预约吗", action="explain", day=3, price=1)
        self.assertEqual(result["state"], "answer")
        self.assertNotIn("operations", result)
        self.assertIn("3402.67", result["message"])
        self.assertIn("12:57", result["message"])

    def test_provider_unavailable_and_invalid_dates_do_not_offer_edits(self):
        def unavailable(*args, **kwargs):
            return ModelReply(error="missing_model_credentials")
        result = interpret_edit({**self.payload, "text": "想换一家饭店"}, Facts(), provider=unavailable)
        self.assertEqual(result["state"], "unavailable")
        result = self.interpret("第九天重新排一下", action="optimize", day=2)
        self.assertEqual(result["state"], "clarify")

    def test_schedule_and_transport_share_existing_actions(self):
        result = self.interpret("第三天顺路排一下", action="optimize", day=2)
        self.assertEqual(result["operations"], [{"action": "optimize", "day": 3}])
        result = self.interpret("第三天去博物馆坐地铁", action="transport", activity="museum", mode="metro")
        self.assertEqual(result["operations"], [{"action": "transport", "activity": "museum", "mode": "metro"}])

    def test_batch_keeps_one_list_of_actions_and_separate_locks(self):
        result = self.interpret("第三天午餐换餐厅甲，改到12:15至13:15，保留博物馆预约",
                                action="batch", day=3, operations=[
                                    {"action": "replace", "activity": "lunch3", "place": "餐厅甲"},
                                    {"action": "time", "activity": "lunch3", "start": "12:15", "end": "13:15"},
                                ])
        self.assertEqual(result["state"], "proposal")
        self.assertNotIn("operations", result)
        self.assertEqual([op["action"] for op in result["proposal"]["operations"]], ["replace", "time"])
        self.assertEqual(result["proposal"]["locks"], [{"name": "上海博物馆", "day": 3, "start": "15:00"}])

    def test_batch_requires_target_confirmation_for_ambiguous_meal(self):
        result = self.interpret("第三天午餐换餐厅乙，去博物馆坐地铁", action="batch", day=3, operations=[
            {"action": "replace", "activity": "lunch3", "place": "餐厅乙"},
            {"action": "transport", "activity": "museum", "mode": "metro"},
        ])
        self.assertEqual(result["state"], "clarify")
        self.assertIn("餐厅乙静安店", result["message"])

    def test_a_lighter_day_can_be_combined_with_a_meal_goal(self):
        result = self.interpret("第三天轻松一些，午餐换附近的本帮菜", action="batch", day=3,
                                pace="lighter", operations=[
                                    {"action": "meal_options", "activity": "lunch3", "cuisine": "本帮菜"},
                                ])
        self.assertEqual(result["state"], "proposal")
        self.assertEqual(result["proposal"]["pace"], "lighter")

    def test_named_cuisine_cannot_be_silently_removed_from_meal_search(self):
        text = "第三天晚上轻松一点，午餐换一家当地特色菜。"
        result = self.interpret(text, action="meal_options", activity="lunch3")
        self.assertEqual(result["state"], "clarify")
        self.assertIn("当地特色菜", result["message"])
        result = self.interpret(text, action="meal_options", activity="lunch3", cuisine="当地特色菜")
        self.assertEqual(result["proposal"]["operations"][0]["cuisine"], "当地特色菜")
        self.assertEqual(result["proposal"]["pace_period"], "evening")

    def test_time_range_after_change_is_the_target_not_the_original_range(self):
        text = "第三天午餐从12:00–13:00改为12:15–13:15，去博物馆坐地铁。"
        transfer = {"action": "transport", "activity": "museum", "mode": "metro"}
        for start, end, state in [("12:15", "13:15", "proposal"), ("12:00", "13:00", "clarify")]:
            with self.subTest(start=start):
                result = self.interpret(text, action="batch", day=3, operations=[
                    {"action": "time", "activity": "lunch3", "start": start, "end": end},
                    transfer,
                ])
                self.assertEqual(result["state"], state)

    def test_batch_refuses_to_retime_a_reservation_or_edit_another_day(self):
        meal = {"action": "meal_options", "activity": "lunch3"}
        for edit in [
            {"action": "time", "activity": "museum", "start": "16:00", "end": "17:00"},
            {"action": "time", "activity": "lunch2", "start": "13:00", "end": "14:00"},
        ]:
            with self.subTest(edit=edit):
                result = self.interpret("第三天换餐馆，再调整到16:00", action="batch", day=3, operations=[meal, edit])
                self.assertEqual(result["state"], "clarify")
                self.assertNotIn("proposal", result)
