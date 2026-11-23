"""Regression coverage for requirement inventory, modality and tag grounding."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from chinatravel.agent.nesy_agent.extraction_alignment import (
    extract_with_repair, grounded_extraction, source_modality,
)
from chinatravel.agent.nesy_agent.constraint_compiler import compile_constraint
from chinatravel.agent.nesy_agent.translation_contract import (
    TranslationValidationError, extraction_output_options,
)

CATALOG={
    'attraction_names':{'故宫博物院','天坛','外滩','上海迪士尼度假区'},
    'restaurant_names':{'甲餐馆','乙餐馆'}, 'hotel_names':{'样例酒店'},
    'spot_type':{'历史古迹','自然风光','博物馆/纪念馆','游乐园/体育娱乐'},
    'food_type':{'本帮菜','北京菜','川菜','火锅','粤菜'},
    'hotel_feature':{'泳池','健身室','影音房'},
}


def query(text,city='上海'):
    return dict(uid='regression',start_city='南京',target_city=city,days=2,people_number=2,nature_language=text)


def output(hard=(),soft=(),unmapped=None):
    result={kind+'_logic':[code for code,source in entries] for kind,entries in (('hard',hard),('soft',soft))}
    for kind,entries in (('hard',hard),('soft',soft)):
        result[kind+'_logic_evidence']=[dict(constraint=code,source=source) for code,source in entries]
    if unmapped is not None:result['unmapped_requirements']=unmapped
    return result


class Replies:
    name='fixture'
    _client_kwargs={'base_url':'http://127.0.0.1:18080/v1'}
    def __init__(self,*values):
        self.values=list(values);self.messages=[];self.options=[]
    def __call__(self,messages,**options):
        self.messages.append(messages);self.options.append(options)
        if not self.values:raise AssertionError('Unexpected additional model call')
        value=self.values.pop(0)
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)


class TranslationFlowTests(unittest.TestCase):
    def setUp(self):
        self.patches=[patch('chinatravel.agent.nesy_agent.extraction_alignment._catalog',return_value=deepcopy(CATALOG)),
                      patch('chinatravel.agent.nesy_agent.constraint_compiler._catalog',return_value=deepcopy(CATALOG))]
        for item in self.patches:item.start();self.addCleanup(item.stop)

    def test_wishes_are_soft_without_mandatory_words(self):
        for text in ('想去故宫','可以去故宫','希望去故宫','喜欢历史古迹','I would like to visit a museum','We can visit a museum'):
            with self.subTest(text=text):self.assertEqual(source_modality(text,text)[0],'soft')

    def test_obligation_overrides_wish(self):
        text='我想一定要去故宫'
        self.assertEqual(source_modality(text,'故宫')[0],'hard')

    def test_numeric_limit_stays_hard_after_polite_hope(self):
        text='希望预算不超过3000元'
        self.assertEqual(source_modality(text,'预算不超过3000元')[0],'hard')

    def test_negative_wish_is_exclusion(self):
        self.assertEqual(source_modality('不想去故宫','故宫')[0],'negative')
        self.assertEqual(source_modality('最好不去故宫','故宫')[0],'negative_soft')

    def test_mixed_clauses_do_not_share_modality(self):
        text='必须去故宫，想吃火锅，预算不超过3000元'
        self.assertEqual(source_modality(text,'故宫')[0],'hard')
        self.assertEqual(source_modality(text,'火锅')[0],'soft')
        self.assertEqual(source_modality(text,'预算不超过3000元')[0],'hard')

    def test_inventory_then_existing_four_field_output(self):
        text='想吃火锅，预算不超过3000元'
        inventory={'requirements':[{'source':'想吃火锅','requirement':'品尝火锅'}, {'source':'预算不超过3000元','requirement':'总预算上限3000元'}]}
        translated=output(hard=[('cost<=3000','预算不超过3000元')],soft=[("{'火锅'}<=food_type",'想吃火锅')],unmapped=[])
        llm=Replies(inventory,translated)
        result=extract_with_repair(query(text),llm,'Translate this request')
        self.assertEqual(len(llm.messages),2)
        self.assertEqual(result['requirement_inventory'],inventory['requirements'])
        self.assertIn("{'火锅'}<=food_type",result['soft_logic'])
        self.assertIn('cost<=3000',result['hard_logic'])
        self.assertIn('想吃火锅',llm.messages[1][0]['content'])
        self.assertEqual(llm.options[0]['response_format']['json_schema']['name'],'requirement_inventory')

    def test_inventory_quote_failure_is_repaired_before_dsl(self):
        text='想吃火锅'
        llm=Replies({'requirements':[{'source':'想吃粤菜','requirement':'吃粤菜'}]},
                    {'requirements':[{'source':text,'requirement':'品尝火锅'}]},
                    output(soft=[("{'火锅'}<=food_type",text)],unmapped=[]))
        result=extract_with_repair(query(text),llm,'Translate')
        self.assertIn("{'火锅'}<=food_type",result['soft_logic'])
        self.assertEqual(len(llm.messages),3)

    def test_dropped_inventory_requirement_requires_repair(self):
        text='想吃火锅，行程轻松一些'
        inventory={'requirements':[{'source':'想吃火锅','requirement':'品尝火锅'},{'source':'行程轻松一些','requirement':'轻松节奏'}]}
        llm=Replies(inventory,output(soft=[("{'火锅'}<=food_type",'想吃火锅')],unmapped=[]),
                    output(soft=[("{'火锅'}<=food_type",'想吃火锅')],unmapped=['行程轻松一些']))
        result=extract_with_repair(query(text),llm,'Translate')
        self.assertEqual(len(llm.messages),3)
        self.assertEqual(result['unmapped_requirements'],['行程轻松一些'])

    def test_unmapped_source_must_be_literal(self):
        text='行程轻松一些'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(unmapped=['行程很轻松']))

    def test_local_cuisine_uses_destination(self):
        text='去上海旅游，想吃当地菜'
        result=grounded_extraction(query(text),output(soft=[("{'本帮菜'}<=food_type",'想吃当地菜')]))
        self.assertIn("{'本帮菜'}<=food_type",result['soft_logic'])

    def test_local_cuisine_alias_normalized_to_catalog(self):
        text='想吃上海菜'
        result=grounded_extraction(query(text),output(soft=[("{'上海菜'}<=food_type",text)]))
        self.assertIn("{'本帮菜'}<=food_type",result['soft_logic'])

    def test_local_cuisine_does_not_accept_unrelated_category(self):
        text='想吃当地菜'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[("{'川菜'}<=food_type",text)]))

    def test_hotpot_uses_cuisine_not_restaurant_name(self):
        text='想吃火锅'
        result=grounded_extraction(query(text),output(soft=[("{'火锅'}<=food_type",text)]))
        self.assertIn("{'火锅'}<=food_type",result['soft_logic'])
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[("{'火锅'}<=restaurant_names",text)]))

    def test_or_preferences_stay_alternatives(self):
        text='可以吃本帮菜或者川菜'
        code="({'本帮菜'}<=food_type or {'川菜'}<=food_type)"
        result=grounded_extraction(query(text),output(soft=[(code,text)]))
        self.assertEqual(result['soft_logic'],[code])

    def test_invalid_or_branch_is_rejected(self):
        text='可以吃本帮菜或者美食'
        code="({'本帮菜'}<=food_type or {'美食'}<=food_type)"
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[(code,text)]))

    def test_excluded_named_place_is_preserved(self):
        text='不想去故宫'
        code="not ({'故宫博物院'}<=attraction_names)"
        result=grounded_extraction(query(text),output(hard=[(code,text)]))
        self.assertIn(code,result['hard_logic'])
        self.assertIsNotNone(compile_constraint(code))

    def test_soft_exclusion_is_soft(self):
        text='最好不去故宫'
        code="not ({'故宫博物院'}<=attraction_names)"
        result=grounded_extraction(query(text),output(soft=[(code,text)]))
        self.assertIn(code,result['soft_logic'])

    def test_negation_must_not_become_positive_requirement(self):
        text='不想去故宫'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(hard=[("{'故宫博物院'}<=attraction_names",text)]))

    def test_bare_symbol_is_rejected(self):
        text='尽量轻松一些'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[('activity_time',text)]))

    def test_unresolved_numeric_symbol_is_rejected(self):
        text='住宿尽量便宜'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[('hotel_price<=min_price',text)]))

    def test_body_contains_inventory_without_overwriting_metadata(self):
        text='想去故宫'
        llm=Replies({'requirements':[{'source':text,'requirement':'参观故宫'}]},output(soft=[("{'故宫博物院'}<=attraction_names",text)],unmapped=[]))
        original=query(text)
        result=extract_with_repair(original,llm,'Translate')
        for key in ('uid','start_city','target_city','days','people_number'):
            self.assertEqual(result[key],original[key])

    def test_default_schema_preserves_four_field_callers(self):
        schema=extraction_output_options(Replies())['response_format']['json_schema']['schema']
        self.assertEqual(set(schema['required']),{'hard_logic','soft_logic','hard_logic_evidence','soft_logic_evidence'})

    def test_numeric_do_not_exceed_is_not_exclusion(self):
        text='人均一顿饭不要超过70元'
        result=grounded_extraction(query(text),output(hard=[('food_price<=70',text)]))
        self.assertIn('food_price<=70',result['hard_logic'])

    def test_explicit_room_count_stays_hard_under_wish(self):
        text='想住两间大床房'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[('rooms==2',text),('room_type==1',text)]))
        result=grounded_extraction(query(text),output(hard=[('rooms==2',text),('room_type==1',text)]))
        self.assertIn('rooms==2',result['hard_logic'])

    def test_conjunction_separates_wish_and_obligation(self):
        text='想去故宫但必须吃火锅'
        self.assertEqual(source_modality(text,'故宫')[0],'soft')
        self.assertEqual(source_modality(text,'火锅')[0],'hard')

    def test_metadata_cannot_weaken_boolean_budget(self):
        text='酒店不超过100元'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(hard=[('(days==2 or hotel_price<=100)',text)]))

    def test_budget_cannot_bind_to_meal_and_hotel_price(self):
        text='预算不超过3000元'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(hard=[('(hotel_price>=3000 or food_price>=3000)',text)]))

    def test_boolean_budget_operator_cannot_be_weakened(self):
        text='预算不超过3000元'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(hard=[('(cost<=3000 or cost>3000)',text)]))

    def test_shared_inventory_quote_does_not_hide_missing_need(self):
        text='希望酒店有泳池并且安静'
        q=query(text)
        q['requirement_inventory']=[dict(source=text,requirement='酒店有泳池'),dict(source=text,requirement='酒店安静')]
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(q,output(soft=[("{'泳池'}<=hotel_feature",text)],unmapped=[]))
        result=grounded_extraction(q,output(soft=[("{'泳池'}<=hotel_feature",text)],unmapped=[text]))
        self.assertEqual(result['unmapped_requirements'],[text])

    def test_conjoined_exclusions_keep_each_negation(self):
        text='必须不去故宫并且不去天坛'
        code="(not ({'故宫博物院'}<=attraction_names) and not ({'天坛'}<=attraction_names))"
        result=grounded_extraction(query(text),output(hard=[(code,text)]))
        self.assertEqual(len(result['hard_logic']),5)

    def test_mode_requires_transport_evidence(self):
        text='想吃火锅'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[("transport_type<={'taxi'}",text)]))

    def test_strong_soft_numeric_target_is_soft(self):
        text='每顿饭尽量不要超过70元'
        result=grounded_extraction(query(text),output(soft=[('food_price<=70',text)]))
        self.assertIn('food_price<=70',result['soft_logic'])

    def test_normal_entry_uses_inventory_and_grounding(self):
        from chinatravel.agent.nesy_agent.nl2sl_hybrid import nl2sl_step1
        text='想吃当地菜'
        llm=Replies({'requirements':[dict(source=text,requirement='品尝目的地当地菜')]},
                    output(soft=[("{'本帮菜'}<=food_type",text)],unmapped=[]))
        result=nl2sl_step1(query(text),llm)
        self.assertIn("{'本帮菜'}<=food_type",result['soft_logic'])
        self.assertEqual(len(llm.messages),2)

    def test_bilingual_examples_use_current_schema_and_wish_policy(self):
        from chinatravel.agent.nesy_agent.prompts import prompts,prompts_en
        for module in (prompts,prompts_en):
            for number in range(1,5):
                sample=getattr(module,'nl2sl_example_'+str(number))
                answer=json.loads(sample.split('Answer:',1)[1].strip())
                self.assertIn('hard_logic_evidence',answer)
                self.assertIn('soft_logic_evidence',answer)
                if number==2:
                    self.assertEqual(len(answer['soft_logic']),2)

    def test_suffix_permission_is_soft(self):
        self.assertEqual(source_modality('故宫或天坛都可以','故宫')[0],'soft')

    def test_cannot_visit_is_negative(self):
        self.assertEqual(source_modality('不可以去故宫','故宫')[0],'negative')
        self.assertEqual(source_modality('不能去故宫','故宫')[0],'negative')

    def test_soft_metadata_without_evidence_is_rejected(self):
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query('去上海旅游'),dict(hard_logic=[],hard_logic_evidence=[],soft_logic=['days==2'],soft_logic_evidence=[]))

    def test_compiled_food_alternative_accepts_either(self):
        from chinatravel.symbol_verification.dsl import execute_dsl_code
        code=compile_constraint("({'本帮菜'}<=food_type or {'川菜'}<=food_type)")
        self.assertIsNotNone(code)
        for cuisine,expected in [('本帮菜',True),('川菜',True),('火锅',False)]:
            values=dict(plan={'cuisine':cuisine},allactivities=lambda p:[{'type':'lunch','cuisine':p['cuisine']}],
                        activity_type=lambda a:a['type'],target_city=lambda p:'上海',restaurant_type=lambda a,c:a['cuisine'])
            execute_dsl_code(code,values,allowed_builtins={'set':set})
            self.assertIs(values['result'],expected)

    def test_compiled_exclusion_checks_absence(self):
        from chinatravel.symbol_verification.dsl import execute_dsl_code
        code=compile_constraint("not ({'故宫博物院'}<=attraction_names)")
        for name,expected in [('天坛',True),('故宫博物院',False)]:
            values=dict(plan={},allactivities=lambda p:[{'type':'attraction','position':name}],
                        activity_type=lambda a:a['type'],activity_position=lambda a:a['position'])
            execute_dsl_code(code,values,allowed_builtins={'set':set})
            self.assertIs(values['result'],expected)

    def test_inventory_recovers_whitespace_without_changing_words(self):
        text='想吃火锅，每顿饭不要超过70元'
        llm=Replies({'requirements':[dict(source='想吃火锅',requirement='火锅'),dict(source='每顿饭不要超过 70 元',requirement='餐费上限')]},
                    output(hard=[('food_price<=70','每顿饭不要超过70元')],soft=[("{'火锅'}<=food_type",'想吃火锅')],unmapped=[]))
        result=extract_with_repair(query(text),llm,'Translate')
        self.assertEqual(result['requirement_inventory'][1]['source'],'每顿饭不要超过70元')
        self.assertEqual(len(llm.messages),2)

    def test_named_place_or_is_not_duplicated(self):
        text='可以去故宫或者天坛'
        code="({'故宫博物院'}<=attraction_names or {'天坛'}<=attraction_names)"
        result=grounded_extraction(query(text),output(soft=[(code,text)]))
        self.assertEqual(result['soft_logic'],[code])

    def test_room_quantity_operator_does_not_change_bed_equality(self):
        for text,code in [('至少两间双床房','rooms>=2'),('最多两间双床房','rooms<=2')]:
            with self.subTest(text=text):
                result=grounded_extraction(query(text),output(hard=[(code,text),('room_type==2',text)]))
                self.assertIn(code,result['hard_logic'])
                self.assertIn('room_type==2',result['hard_logic'])

    def test_upper_bound_synonyms_stay_hard(self):
        for word in ('不高于','最多','不能超过'):
            text='希望酒店每晚'+word+'500元'
            with self.subTest(text=text):
                result=grounded_extraction(query(text),output(hard=[('hotel_price<=500',text)]))
                self.assertIn('hotel_price<=500',result['hard_logic'])

    def test_numeric_values_are_bound_to_their_fields(self):
        text='酒店每晚不超过500元且每餐不超过100元'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(hard=[('hotel_price<=100',text),('food_price<=500',text)]))
        result=grounded_extraction(query(text),output(hard=[('hotel_price<=500',text),('food_price<=100',text)]))
        self.assertIn('food_price<=100',result['hard_logic'])

    def test_food_and_taxi_negations_do_not_become_positive(self):
        for text,field,value in [('不吃川菜','food_type','川菜'),('市内不打车','transport_type','taxi')]:
            positive="{'"+value+"'}<="+field
            with self.subTest(text=text):
                with self.assertRaises(TranslationValidationError):
                    grounded_extraction(query(text),output(hard=[(positive,text)]))
                result=grounded_extraction(query(text),output(hard=[('not ('+positive+')',text)]))
                self.assertTrue(any(code.startswith('not ') for code in result['hard_logic']))

    def test_failed_and_attempt_does_not_pollute_repaired_or(self):
        text='必须吃本帮菜或者川菜'
        code="({'本帮菜'}<=food_type or {'川菜'}<=food_type)"
        llm=Replies({'requirements':[dict(source=text,requirement='两种菜系任选其一')]},
                    output(hard=[("{'本帮菜'}<=food_type",text),("{'川菜'}<=food_type",text)],unmapped=[]),
                    output(hard=[(code,text)],unmapped=[]))
        result=extract_with_repair(query(text),llm,'Translate')
        self.assertEqual(len(llm.messages),3)
        self.assertEqual(len(result['hard_logic']),5)
        self.assertIn(code,result['hard_logic'])

    def test_explicit_cuisine_city_overrides_destination(self):
        text='想吃北京当地菜'
        result=grounded_extraction(query(text),output(soft=[("{'北京菜'}<=food_type",text)]))
        self.assertIn("{'北京菜'}<=food_type",result['soft_logic'])
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[("{'本帮菜'}<=food_type",text)]))

    def test_normal_english_entry_keeps_two_stages(self):
        from chinatravel.agent.nesy_agent.nl2sl_hybrid import nl2sl_step1
        catalog=deepcopy(CATALOG);catalog['food_type']={'Shanghai cuisine','Sichuan cuisine','Hot pot'}
        text='I would like to try local cuisine'
        q=query(text,'Shanghai');q['start_city']='Nanjing'
        llm=Replies({'requirements':[dict(source=text,requirement='Try local cuisine')]},output(soft=[("{'Shanghai cuisine'}<=food_type",text)],unmapped=[]))
        with patch('chinatravel.agent.nesy_agent.extraction_alignment._catalog',return_value=catalog),patch('chinatravel.agent.nesy_agent.constraint_compiler._catalog',return_value=catalog):
            result=nl2sl_step1(q,llm,lang='en')
        self.assertIn("{'Shanghai cuisine'}<=food_type",result['soft_logic'])
        self.assertIn('Collect every travel requirement',llm.messages[0][0]['content'])

    def test_legacy_translation_cache_is_refreshed(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from chinatravel.agent.nesy_agent.nesy_agent import NesyAgent
        from chinatravel.agent.nesy_agent.constraint_compiler import template_programs
        from chinatravel.agent.nesy_agent.extraction_alignment import TRANSLATION_FLOW_VERSION
        text='预算不超过3000元'
        q=query(text)
        fresh=dict(q,**grounded_extraction(q,output(hard=[('cost<=3000',text)],unmapped=[])))
        fresh['translation_flow_version']=TRANSLATION_FLOW_VERSION
        fresh['requirement_inventory']=[dict(source=text,requirement='总预算上限')]
        fresh['hard_logic_py']=template_programs(fresh)
        with tempfile.TemporaryDirectory() as cache:
            folder=Path(cache)/'translation_fixture_reflect';folder.mkdir()
            file=folder/'regression.json'
            file.write_text(json.dumps(dict(q,hard_logic=[],hard_logic_py=[])),encoding='utf-8')
            agent=SimpleNamespace(cache_dir=cache,backbone_llm=Replies(),lang='zh',translation_mode='grounded')
            with patch('chinatravel.agent.nesy_agent.nesy_agent.nl2sl_reflect',return_value=deepcopy(fresh)) as translated:
                result=NesyAgent.translate_nl2sl(agent,deepcopy(q),load_cache=True)
            translated.assert_called_once()
            self.assertEqual(result['translation_flow_version'],TRANSLATION_FLOW_VERSION)
            self.assertEqual(json.loads(file.read_text(encoding='utf-8'))['translation_flow_version'],TRANSLATION_FLOW_VERSION)

    def test_exhausted_translation_keeps_grounded_constraints_without_caching(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from chinatravel.agent.nesy_agent.nesy_agent import NesyAgent

        text = '坐火车往返，预算3000元，想吃本帮菜'
        source = query(text)
        llm = Replies({}, {}, {})
        with TemporaryDirectory() as cache:
            agent = SimpleNamespace(cache_dir=cache, backbone_llm=llm, lang='zh', translation_mode='grounded')
            result = NesyAgent.translate_nl2sl(agent, source)
            self.assertFalse(list(Path(cache).rglob('*.json')))
        self.assertEqual(len(llm.messages), 3)
        self.assertEqual(result['translation_fallback']['status'], 'needs_review')
        self.assertEqual(result['translation_fallback']['original_request'], text)
        self.assertIn('days==2', result['hard_logic'])
        self.assertIn("intercity_transport=={'train'}", result['hard_logic'])
        self.assertIn('cost<=3000', result['hard_logic'])
        self.assertEqual(result['hard_logic_py'], [compile_constraint(code) for code in result['hard_logic']])

    def test_fallback_does_not_force_unresolved_alternatives(self):
        from chinatravel.agent.nesy_agent.extraction_alignment import fallback_translation

        source = query('坐火车或者飞机都行')
        result = fallback_translation(source, {}, TranslationValidationError('Missing requirement'))
        self.assertEqual(result['hard_logic'], ['days==2', 'people_number==2', 'tickets==2', 'taxi_cars==1'])
        self.assertEqual(result['unmapped_requirements'], [source['nature_language']])

    def test_vague_pace_does_not_create_numeric_duration(self):
        text='尽量轻松一些'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[('activity_time<=60',text)]))

    def test_vague_pace_does_not_create_a_clock_deadline(self):
        text='尽量轻松一些'
        with self.assertRaises(TranslationValidationError):
            grounded_extraction(query(text),output(soft=[("activity_end_time<='20:00'",text)]))

    def test_english_local_cuisine(self):
        catalog=deepcopy(CATALOG);catalog['food_type']={'Shanghai cuisine','Sichuan cuisine','Hot pot'}
        text='I would like to try local cuisine'
        with patch('chinatravel.agent.nesy_agent.extraction_alignment._catalog',return_value=catalog),patch('chinatravel.agent.nesy_agent.constraint_compiler._catalog',return_value=catalog):
            result=grounded_extraction(query(text,'Shanghai'),output(soft=[("{'Shanghai cuisine'}<=food_type",text)]))
        self.assertIn("{'Shanghai cuisine'}<=food_type",result['soft_logic'])


if __name__=='__main__':unittest.main()
