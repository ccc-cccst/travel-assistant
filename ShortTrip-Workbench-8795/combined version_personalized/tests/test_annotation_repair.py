"""Regression tests for errors observed in the 604-query annotation run."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests.test_requirement_extraction_flow import CATALOG, Replies, output, query
from chinatravel.agent.nesy_agent.extraction_alignment import extract_with_repair, source_modality
from chinatravel.agent.nesy_agent.translation_contract import TranslationValidationError


class AnnotationRepairTests(unittest.TestCase):
    def setUp(self):
        for name in ('extraction_alignment', 'constraint_compiler'):
            handle = patch('chinatravel.agent.nesy_agent.' + name + '._catalog', return_value=deepcopy(CATALOG))
            handle.start()
            self.addCleanup(handle.stop)

    def test_metadata_inventory_does_not_need_extra_dsl(self):
        text = '当前位置南京。我和朋友两个人想去上海玩2天，想吃火锅'
        inventory = {'requirements': [
            {'source': '当前位置南京', 'requirement': '出发城市南京'},
            {'source': '我和朋友两个人想去上海玩2天', 'requirement': '两人去上海玩两天'},
            {'source': '想吃火锅', 'requirement': '吃火锅'}]}
        llm = Replies(inventory, output(soft=[("{'火锅'}<=food_type", '想吃火锅')], unmapped=[]))
        result = extract_with_repair(query(text), llm, 'Translate')
        self.assertEqual(len(result['requirement_inventory']), 1)

    def test_inventory_description_is_rebound_only_to_unique_source(self):
        text = '想吃平安悦坊附近的樨香'
        inventory = {'requirements': [{'source': text, 'requirement': '在平安悦坊附近品尝樨香'}]}
        llm = Replies(inventory, output(unmapped=['在平安悦坊附近品尝樨香']))
        result = extract_with_repair(query(text), llm, 'Translate')
        self.assertEqual(result['unmapped_requirements'], [text])

    def test_unknown_unmapped_paraphrase_remains_rejected(self):
        text = '希望酒店安静'
        inventory = {'requirements': [{'source': text, 'requirement': '住宿安静'}]}
        value = output(unmapped=['远离所有景点'])
        with self.assertRaises(TranslationValidationError):
            extract_with_repair(query(text), Replies(inventory, value, value, value), 'Translate')

    def test_compound_context_keeps_real_preference(self):
        text = '我想去上海玩2天并吃火锅'
        inventory = {'requirements': [{'source': text, 'requirement': '上海两天且吃火锅'}]}
        value = output(unmapped=[])
        with self.assertRaises(TranslationValidationError):
            extract_with_repair(query(text), Replies(inventory, value, value, value), 'Translate')

    def test_wish_to_taste_is_soft(self):
        text = '想品尝上海本地特色菜'
        self.assertEqual(source_modality(text, text, field='food_type')[0], 'soft')

    def test_english_friends_requests_are_not_removed_as_metadata(self):
        from chinatravel.agent.nesy_agent.requirement_inventory import metadata_only_source
        for text in ('I would like to visit friends', 'I would like to stay with friends'):
            self.assertFalse(metadata_only_source(query(text), text))

    def test_nested_hotel_name_does_not_require_an_attraction(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        catalog = deepcopy(CATALOG)
        catalog['attraction_names'].add('南山')
        catalog['hotel_names'].add('深圳南山科技园希尔顿惠庭酒店')
        entries = coverage(query('住深圳南山科技园希尔顿惠庭酒店', '深圳'), catalog)
        self.assertEqual([entry['field'] for entry in entries], ['hotel_names'])

    def test_unique_city_prefix_is_resolved_without_fuzzy_selection(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import resolve_name
        catalog = deepcopy(CATALOG)
        catalog['attraction_names'] = {'成都动物园', '天坛'}
        self.assertEqual(resolve_name('动物园', 'attraction_names', catalog), ({'成都动物园'}, False))
        catalog['attraction_names'].add('成都野生动物园')
        with self.assertRaises(TranslationValidationError):
            resolve_name('动物园', 'attraction_names', catalog)

    def test_old_translation_cache_is_invalidated(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import translation_cache_matches
        item = query('想品尝上海本地特色菜')
        old = dict(item, translation_flow_version=2, requirement_inventory=[])
        self.assertFalse(translation_cache_matches(item, old))

    def test_unmapped_context_keeps_children_without_inventing_preference(self):
        text = '我带我孩子想去上海玩2天'
        inventory = {'requirements': [{'source': text, 'requirement': '带孩子前往上海两天'}]}
        result = extract_with_repair(query(text), Replies(inventory, output(unmapped=[])), 'Translate')
        self.assertEqual(result['unmapped_requirements'], [text])
        self.assertEqual(result['soft_logic'], [])



    def test_chinese_budget_cannot_be_silently_omitted(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import grounded_extraction
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query('总预算一万元'), output(unmapped=[]))


    def test_friends_stay_survives_even_with_duration_metadata(self):
        from chinatravel.agent.nesy_agent.requirement_inventory import metadata_only_source
        for text in ('I would like to stay with friends for two days', 'I would like to visit friends Shanghai'):
            self.assertFalse(metadata_only_source(query(text), text))

    def test_longer_hotel_feature_does_not_require_shorter_feature(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        catalog = deepcopy(CATALOG)
        catalog['hotel_feature'] = {'泳池', '网红泳池'}
        entries = coverage(query('酒店要有网红泳池'), catalog)
        self.assertEqual([entry['code'] for entry in entries], ["{'网红泳池'}<=hotel_feature"])

    def test_nearby_lodging_anchor_is_not_a_visit_request(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        entries = coverage(query('住样例酒店，在故宫博物院附近', '北京'), CATALOG)
        self.assertFalse(any(entry['field'] == 'attraction_names' for entry in entries))

    def test_mandatory_visit_survives_nearby_lodging_wording(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        for text in ('故宫必须去', '迪士尼一定要去', '酒店附近的故宫博物院必须去'):
            entries = coverage(query(text), CATALOG)
            self.assertTrue(any(entry['field'] == 'attraction_names' for entry in entries), text)

    def test_unknown_compound_name_does_not_force_known_prefix_poi(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        catalog = deepcopy(CATALOG)
        catalog['attraction_names'] = {'金牛湖风景区'}
        entries = coverage(query('想去金牛湖野生动物园'), catalog, modality='soft')
        self.assertEqual(entries, [])

    def test_museum_category_is_not_forced_to_a_single_city_museum(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        catalog = deepcopy(CATALOG)
        catalog['attraction_names'] = {'重庆美术馆'}
        catalog['spot_type'].add('美术馆/艺术馆')
        entries = coverage(query('想去参观一些美术馆', '重庆'), catalog, modality='soft')
        self.assertEqual([entry['field'] for entry in entries], ['spot_type'])

    def test_current_location_and_solo_trip_are_basic_context(self):
        from chinatravel.agent.nesy_agent.requirement_inventory import metadata_only_source
        for text in ('我目前在南京', '计划独自前往上海游玩两天'):
            self.assertTrue(metadata_only_source(query(text), text))



    def test_wish_marker_allows_adverbs_and_city_context(self):
        for text, source in [('想重点参观博物馆', '博物馆'), ('我想在上海品尝当地菜', '当地菜')]:
            self.assertEqual(source_modality(text, source)[0], 'soft')



    def test_alias_index_is_cached_without_shared_mutation(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import aliases, _alias_entries
        _alias_entries.cache_clear()
        first = aliases({'故宫博物院', '天坛'})
        first['故宫'].add('错误景点')
        second = aliases({'天坛', '故宫博物院'})
        self.assertEqual(second['故宫'], {'故宫博物院'})
        self.assertEqual(_alias_entries.cache_info().hits, 1)
        self.assertEqual(_alias_entries.cache_info().misses, 1)



    def test_literal_name_patterns_are_reused(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import _literal_pattern
        pattern = _literal_pattern('米桃-百花山房·桃花')
        self.assertIs(pattern, _literal_pattern('米桃-百花山房·桃花'))
        self.assertEqual([match.span() for match in pattern.finditer('想吃米桃-百花山房·桃花')], [(2, 12)])


    def test_total_budget_cannot_use_passenger_quantity_as_money(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import grounded_extraction
        text = '预算两个人共3000左右'
        result = grounded_extraction(query(text), output(hard=[('cost<=3000', text)]))
        self.assertIn('cost<=3000', result['hard_logic'])
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text), output(hard=[('cost<=2', text)]))

    def test_rmb_budget_remains_a_total_budget(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        self.assertIn('cost<=3000', [item['code'] for item in coverage(query('预算3000人民币'), CATALOG)])

    def test_expense_wording_is_valid_total_cost_evidence(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import grounded_extraction
        text = '花费不要超过7400元'
        result = grounded_extraction(query(text), output(hard=[('cost<=7400', text)]))
        self.assertIn('cost<=7400', result['hard_logic'])

    def test_meal_price_can_omit_currency_unit(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import grounded_extraction
        text = '人均吃饭每顿不超过80'
        result = grounded_extraction(query(text), output(hard=[('food_price<=80', text)]))
        self.assertIn('food_price<=80', result['hard_logic'])

    def test_meal_duration_is_not_accepted_as_a_price(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import grounded_extraction
        text = '每顿吃饭不超过80分钟'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text), output(hard=[('food_price<=80', text)]))

    def test_hotel_budget_is_not_inserted_as_total_cost(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import grounded_extraction
        text = '酒店每晚预算300'
        result = grounded_extraction(query(text), output(hard=[('hotel_price<=300', text)]))
        self.assertNotIn('cost<=300', result['hard_logic'])

    def test_basic_context_prefix_is_trimmed_from_mixed_inventory(self):
        text = '我想3个人去北京玩5天，喜欢历史古迹'
        q = query(text, '北京')
        q.update(days=5, people_number=3)
        inventory = {'requirements': [{'source': text, 'requirement': '北京五天历史游'}]}
        result = extract_with_repair(q, Replies(inventory, output(soft=[("{'历史古迹'}<=spot_type", '喜欢历史古迹')], unmapped=[])), 'Translate')
        self.assertEqual(result['requirement_inventory'][0]['source'], '喜欢历史古迹')

    def test_context_filter_does_not_drop_conflicting_days(self):
        from chinatravel.agent.nesy_agent.requirement_inventory import metadata_only_source
        self.assertFalse(metadata_only_source(query('我想去上海玩5天'), '我想去上海玩5天'))

    def test_conditional_clauses_survive_metadata_trimming(self):
        from chinatravel.agent.nesy_agent.requirement_inventory import trim_metadata_edges
        text = '如果天气好，想去故宫'
        self.assertEqual(trim_metadata_edges(query(text), text), text)

    def test_brand_branches_are_alternatives(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import resolve_name
        catalog = deepcopy(CATALOG)
        catalog['hotel_names'] = {'样例酒店(甲店)', '样例酒店(乙店)'}
        self.assertEqual(resolve_name('样例酒店', 'hotel_names', catalog), (catalog['hotel_names'], True))

    def test_dining_landmark_is_only_location_context(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import location_anchor
        text = '想人民公园附近的鹤鸣茶社'
        self.assertTrue(location_anchor(text, 1, 5, 'attraction_names'))
        for text in ('酒店附近的故宫必须去', '酒店附近的故宫不能去'):
            start = text.index('故宫')
            self.assertFalse(location_anchor(text, start, start + 2, 'attraction_names'))

    def test_campus_word_in_lodging_location_is_not_a_visit(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import coverage
        catalog = deepcopy(CATALOG)
        catalog['spot_type'].add('大学校园')
        entries = coverage(query('住在华中科技大学附近的样例酒店'), catalog)
        self.assertFalse(any(item['field'] == 'spot_type' for item in entries))



if __name__ == '__main__':
    unittest.main()
