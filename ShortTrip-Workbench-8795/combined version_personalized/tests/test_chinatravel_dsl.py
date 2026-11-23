import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from chinatravel.agent.nesy_agent.chinatravel_dsl import FORMAT, extract_hard_logic
from chinatravel.agent.nesy_agent.nesy_agent import NesyAgent


class Model:
    name = "fixture"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.answer


class OriginalDSLTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.query = dict(uid="easy-test", days=2, people_number=1, start_city="南京",
                          target_city="杭州", nature_language="南京到杭州两天，想去西湖风景名胜区。")
        self.agent = NesyAgent.__new__(NesyAgent)
        self.agent.lang = "zh"
        self.agent.cache_dir = self.tmp.name

    def test_default_uses_original_format_and_ignores_oracle_and_model_metadata(self):
        self.agent.backbone_llm = Model(json.dumps({
            "hard_logic": ["days==3", "{'西湖风景名胜区'}<=attraction_names"],
            "start_city": "广州", "target_city": "重庆", "people_number": 6,
        }, ensure_ascii=False))
        with patch.dict(os.environ):
            os.environ.pop("CHINATRAVEL_TRANSLATION_MODE", None)
            value = self.agent.translate_nl2sl({**self.query, "hard_logic_py": ["result=False"]})
        self.assertEqual(value["translation_mode"], FORMAT)
        self.assertEqual(value["start_city"], "南京")
        self.assertIn("days==2", value["hard_logic"])
        self.assertNotIn("days==3", value["hard_logic"])
        self.assertIn("{'西湖风景名胜区'}<=attraction_names", value["hard_logic"])
        self.assertEqual(len(value["hard_logic"]), len(value["hard_logic_py"]))
        self.assertNotIn("result=False", value["hard_logic_py"])
        (messages, opts), = self.agent.backbone_llm.calls
        self.assertIn("'hard_logic':", messages[0]["content"])
        self.assertNotIn("hard_logic_evidence", messages[0]["content"])
        self.assertTrue(opts["json_mode"])

    def test_uses_separate_validated_cache_and_refreshes_when_input_changes(self):
        prompt = '{"hard_logic":["cost<=1800"]}'
        self.agent.backbone_llm = Model(prompt)
        self.agent.translation_mode = "chinatravel"
        older = Path(self.tmp.name) / "translation_fixture_reflect" / "easy-test.json"
        older.parent.mkdir()
        older.write_text('{"hard_logic":["cost<=1"]}', encoding="utf-8")
        first = self.agent.translate_nl2sl(self.query, load_cache=True)
        second = self.agent.translate_nl2sl(self.query, load_cache=True)
        modified = self.agent.translate_nl2sl({**self.query, "nature_language": "预算1800元，杭州两日游"}, load_cache=True)
        self.assertEqual(first["hard_logic"], second["hard_logic"])
        self.assertIn("cost<=1800", second["hard_logic"])
        self.assertNotIn("cost<=1", second["hard_logic"])
        self.assertEqual(modified["nature_language"], "预算1800元，杭州两日游")
        self.assertEqual(len(self.agent.backbone_llm.calls), 2)

    def test_invalid_reply_yields_a_reported_fallback_without_caching(self):
        self.agent.backbone_llm = Model('{"hard_logic":[]}')
        self.agent.translation_mode = "chinatravel"
        value = self.agent.translate_nl2sl(self.query)
        self.assertEqual(value["translation_fallback"]["status"], "needs_review")
        self.assertTrue(value["hard_logic_py"])
        self.assertFalse(list(Path(self.tmp.name).rglob("*.json")))

    def test_a_verbatim_restaurant_name_uses_the_original_restaurant_names_field(self):
        request = {**self.query, "target_city": "重庆", "nature_language": "我想吃久久日本料理。"}
        model = Model(json.dumps({"hard_logic": ["{'久久日本料理'}<=food_type"]}, ensure_ascii=False))
        hard_logic = extract_hard_logic(request, model, "zh")
        self.assertIn("{'久久日本料理'}<=restaurant_names", hard_logic)
        self.assertNotIn("{'久久日本料理'}<=food_type", hard_logic)

    def test_zero_placeholders_do_not_become_new_budget_or_room_limits(self):
        model = Model('{"hard_logic":["days==2","cost<=0","rooms==0"]}')
        self.assertNotIn("cost<=0", extract_hard_logic(self.query, model, "zh"))
        self.assertNotIn("rooms==0", extract_hard_logic(self.query, model, "zh"))
        explicit = {**self.query, "nature_language": "杭州两日游，预算为0元。"}
        self.assertIn("cost<=0", extract_hard_logic(explicit, model, "zh"))

    def test_room_defaults_from_people_are_not_hard_constraints(self):
        model=Model('{"hard_logic":["rooms==1","room_type==2"]}')
        query={**self.query,"people_number":2,"nature_language":"我和朋友两人去杭州玩两天。"}
        constraints=extract_hard_logic(query,model,"zh")
        self.assertNotIn("rooms==1",constraints)
        self.assertNotIn("room_type==2",constraints)
        occupancy={**query,"nature_language":"两个人住双人间。"}
        constraints=extract_hard_logic(occupancy,model,"zh")
        self.assertNotIn("rooms==1",constraints)
        self.assertNotIn("room_type==2",constraints)

    def test_explicit_room_count_and_bed_type_are_preserved(self):
        query={**self.query,"nature_language":"住一间双床房。"}
        model=Model('{"hard_logic":["rooms==1","room_type==2"]}')
        constraints=extract_hard_logic(query,model,"zh")
        self.assertIn("rooms==1",constraints)
        self.assertIn("room_type==2",constraints)
        english={**self.query,"nature_language":"Book one twin room."}
        constraints=extract_hard_logic(english,model,"zh")
        self.assertIn("rooms==1",constraints)
        self.assertIn("room_type==2",constraints)

    def test_room_inference_uses_the_same_source_check(self):
        from chinatravel.agent.nesy_agent.llm_driven_rec import LLMDrivenAgent
        self.agent.backbone_llm=Model('RoomInfo: [1, 2]')
        self.agent.llm_inference_time_count=0
        self.agent.llm_rec_count=0
        self.agent.llm_rec_format_error=0
        query={**self.query,"nature_language":"两个人去杭州玩。"}
        self.agent.query=query
        self.assertEqual(LLMDrivenAgent.decide_rooms(self.agent,query),(None,None))
        query['nature_language']='住一间双床房。'
        self.assertEqual(LLMDrivenAgent.decide_rooms(self.agent,query),(1,2))


if __name__ == "__main__":
    unittest.main()

