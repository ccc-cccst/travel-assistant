"""Integration checks for the installed eleven-city profile."""

import os
import unittest


@unittest.skipUnless(os.environ.get("CHINATRAVEL_PROFILE") == "expanded11",
                     "Use the sandbox CLI to select the eleven-city profile")
class SandboxProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from chinatravel.environment.sandbox.sandbox_adapter import Sandbox
        from chinatravel.environment.world_env import WorldEnv

        cls.env = WorldEnv(lang="zh")
        cls.sandbox = Sandbox("G3")

    def test_native_english_and_chinese_profile_load_in_same_process(self):
        from chinatravel.environment.world_env import WorldEnv

        self.assertEqual(len(WorldEnv(lang="en").support_cities), 10)
        self.assertEqual(len(self.env.support_cities), 11)
        records = self.env.catalog_records("上海", "attraction", "上海迪士尼度假区")
        self.assertTrue(records[0]["tags"])
        self.assertTrue(records[0]["source_locator"])

    def test_missing_hotel_prices_excluded_and_estimate_interval_preserved(self):
        hotels = self.env.accommodations.data["西安"]
        self.assertTrue((hotels["price"] >= 0).all())
        self.assertFalse(hotels["price_status"].eq("unknown").any())
        known_estimate = self.sandbox.catalog_entity("accommodation:xian:67")["payload"]
        access = known_estimate["access"]
        self.assertTrue(access["price_source_status"].startswith("estimated"))
        interval = access["estimated_price_interval"]
        self.assertLessEqual(interval["price_low"], access["estimated_price_reference"])
        self.assertGreaterEqual(interval["price_high"], access["estimated_price_reference"])
        self.assertFalse(access["price_usable_for_static_budget"])

    def test_route_and_conditional_fares(self):
        result = self.sandbox.intercity_search("成都", "西安", "train")
        self.assertFalse(result["errors"])
        self.assertTrue(any(x["record"]["name"] == "G322" for x in result["records"]))
        self.assertTrue(result["conditional_records"])
        route = self.sandbox.goto("西安", "西安北", "老赵家炖牛肉丸子烩菜(大秦店)", "taxi", "11:00")
        self.assertTrue(route["available"], route["error"])
        self.assertGreater(route["legs"][0]["cost"], 0)

    def test_flight_reference_applies_to_observed_date_only(self):
        tool = self.env.intercitytransport
        self.assertTrue(tool.reference_flights("成都", "西安", "2026-11-16"))
        self.assertEqual(tool.reference_flights("成都", "西安", "2026-11-17"), [])
