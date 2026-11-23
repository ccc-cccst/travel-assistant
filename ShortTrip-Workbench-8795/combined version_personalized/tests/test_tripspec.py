"""Verify P007 interpretation independently of model wording and network availability."""
from datetime import date
import json
from pathlib import Path
import unittest

from chinatravel.personalization.tripspec import (
    ModelReply, interpret, public_input, resolve_conflicts,
)


ROOT=Path(__file__).resolve().parents[1]
P007=ROOT/'result/experiments/P007_tripspec_evaluation'
CITIES={'南京','北京','深圳','武汉','上海','成都','广州','重庆','苏州','杭州'}


class MissingPeopleTests(unittest.TestCase):
    def test_clarification_preserves_original_text_and_checks_input(self):
        raw='从广州出发去杭州玩3天'
        spec=interpret({'raw_text':raw},CITIES,canned())
        issue, = spec['issues']
        self.assertEqual((issue['field'],issue['input_type']),('people','number'))
        for invalid in (0,2.5,True,'2'):
            with self.assertRaises(ValueError):
                resolve_conflicts(spec,{issue['id']:invalid})
        updated=resolve_conflicts(spec,{issue['id']:2})
        self.assertTrue(updated['ready'])
        self.assertEqual(updated['raw_text'],raw)
        self.assertEqual(next(a['value'] for a in updated['items'] if a['field']=='people'),2)


def gold(number):
    with (P007/'GOLD_DRAFT.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line)
            if row['record_no']==number:
                return row['raw_text']
    raise KeyError(number)


def canned(*items):
    def reply(_text,_form,_cities):
        return ModelReply(draft={'items':list(items),'questions':[]},attempts=1)
    return reply


def field(spec, name):
    return next(a for a in spec['items'] if a['field']==name)


class TripSpecTests(unittest.TestCase):
    def test_generic_sightseeing_is_context_and_specific_scenery_stays_a_preference(self):
        form = {'start_city': '成都', 'target_city': '南京', 'days': 3, 'people_number': 1}
        for phrase, expected in (('观赏景点', 'trip_context'), ('自然风光', 'experience_theme')):
            with self.subTest(phrase=phrase):
                text = f'我想去南京，哪里适合{phrase}？'
                spec = interpret({'raw_text': text, 'form': form}, CITIES, canned(
                    {'f': 'experience_theme', 'op': 'prefer', 'v': phrase, 's': 'local_experience',
                     'u': None, 'k': 'soft', 'q': f'哪里适合{phrase}'}))
                item = field(spec, expected)
                self.assertEqual(item['value'], phrase)
                self.assertEqual(item['source']['text'], f'哪里适合{phrase}')
                if expected == 'trip_context':
                    self.assertFalse(any(row['field'] == 'experience_theme' for row in spec['items']))
                else:
                    self.assertEqual(item['strength'], 'soft')

    def test_relative_date_uses_the_reference_day_when_supplied(self):
        payload = {'raw_text': '明天去北京', 'form': {'start_city': '深圳', 'target_city': '北京',
                                                 'days': 2, 'people_number': 1}}
        provider = canned({'f': 'start_date', 'op': 'eq', 'v': '明天', 'u': None,
                           's': 'total_trip', 'k': 'fact', 'q': '明天'})
        online = interpret(payload, CITIES, provider, reference_date=date(2026, 12, 31))
        self.assertTrue(online['ready'])
        self.assertEqual(field(online, 'start_date')['value'], '2027-01-01')
        self.assertEqual(field(online, 'start_date')['source']['text'], '明天')
        offline = interpret(payload, CITIES, provider)
        self.assertTrue(offline['ready'])
        self.assertEqual(field(offline, 'start_date')['value'], '明天')

    def test_pure_form_retains_fixed_appointment_without_api(self):
        form=json.loads((ROOT/'examples/four_day_appointment.json').read_text(encoding='utf-8'))
        spec=interpret(form,CITIES,lambda *_:self.fail('纯表单不应发模型请求'))
        self.assertTrue(spec['ready'])
        self.assertEqual(spec['model']['attempts'],0)
        self.assertEqual(field(spec,'appointment')['value'],form['appointments'][0])
        self.assertEqual(field(spec,'intercity_mode')['value'],'火车')
        self.assertFalse(any(a['field']=='budget' for a in spec['items']))

    def test_explicit_total_budget_is_retained_even_if_draft_omits_it(self):
        text=gold(7)
        spec=interpret({'raw_text':text},CITIES,canned(
            {'f':'room_type','op':'eq','v':'双床房','u':None,'s':'total_trip','k':'fact','q':'两间双床房'},
            {'f':'room_count','op':'eq','v':2,'u':'room','s':'total_trip','k':'fact','q':'两间双床房'},
        ))
        self.assertTrue(spec['ready'])
        self.assertEqual(spec['raw_text'],text)
        self.assertEqual((field(spec,'budget')['value'],field(spec,'budget')['unit']),(7400,'CNY/group'))
        self.assertEqual((field(spec,'room_type')['strength'],field(spec,'room_count')['value']),('fact',2))
        self.assertEqual(field(spec,'people')['value'],4)

    def test_scoped_budget_keeps_exclusions_and_can_coexist_with_total_budget(self):
        payload = {'raw_text': '人均预算1500 元（不包括车票住宿）。',
                   'form': {'start_city': '武汉', 'target_city': '南京', 'days': 4,
                            'people_number': 2, 'budget': 5000}}
        spec = interpret(payload, CITIES, canned(
            {'f':'budget','op':'lte','v':1500,'u':'CNY/person','s':'total_trip','k':'hard','q':'人均预算1500 元'}))
        self.assertTrue(spec['ready'])
        budgets = {a['scope']: a for a in spec['items'] if a['field'] == 'budget'}
        self.assertEqual(set(budgets), {'total_trip', 'without_intercity_and_lodging'})
        local = budgets['without_intercity_and_lodging']
        self.assertEqual((local['value'], local['unit']), (1500, 'CNY/person'))
        self.assertIn('不包括车票住宿', local['source']['text'])

    def test_other_budget_exclusions_are_not_treated_as_total(self):
        spec = interpret({'raw_text': '当前位置武汉，去南京玩2天，两个人，预算1200（不含门票）。'},
                         CITIES, canned())
        self.assertEqual(field(spec, 'budget')['scope'], 'unresolved_budget_scope')

    def test_same_text_and_form_values_preserve_both_sources(self):
        spec=interpret({'raw_text':gold(7),'form':{'people_number':4}},CITIES,canned())
        self.assertTrue(spec['ready'])
        people=field(spec,'people')
        self.assertEqual(people['source']['type'],'form')
        self.assertEqual(people['additional_sources'][0]['text'],'四个人')

    def test_contradictory_original_dates_need_confirmation(self):
        text=gold(132)
        spec=interpret({'raw_text':text},CITIES,canned())
        question=next(q for q in spec['issues'] if q['field']=='days')
        self.assertFalse(spec['ready'])
        self.assertEqual([x['value'] for x in question['choices']],[5,7])
        new=resolve_conflicts(spec,{question['id']:1})
        self.assertEqual(new['request_revision'],2)
        self.assertEqual(field(new,'days')['value'],7)
        self.assertEqual(field(new,'days')['additional_sources'][-1]['type'],'user_confirmation')
        self.assertEqual(new['raw_text'],text)
        self.assertFalse(spec['ready'])

    def test_model_timeout_keeps_filled_fields_and_blocks(self):
        spec=interpret({'raw_text':gold(15),'form':{'budget':5000}},CITIES,
                       lambda *_:ModelReply(error='APITimeoutError',attempts=1))
        self.assertFalse(spec['ready'])
        self.assertEqual(field(spec,'budget')['value'],5000)
        self.assertIn('APITimeoutError',[issue['code'] for issue in spec['issues']])

    def test_unknown_model_source_cannot_add_a_hard_requirement(self):
        spec=interpret({'raw_text':gold(7)},CITIES,canned(
            {'f':'nights','op':'eq','v':7,'u':'night','s':'lodging','k':'hard','q':'要求住七晚'},
        ))
        self.assertFalse(any(a['field']=='nights' for a in spec['items']))
        self.assertFalse(spec['ready'])

    def test_whitelist_rejects_reference_columns_even_in_appointments(self):
        text,form=public_input({'raw_text':gold(7),'hard_logic_py':'private', 'form':{
            'budget':7400,'preferences':'private','appointments':[{'name':'地名','hard_logic_py':'private'}]}})
        self.assertEqual(text,gold(7))
        self.assertEqual(form,{'budget':7400,'appointments':[{'name':'地名'}]})

    def test_destination_does_not_match_city_prefix_inside_an_attraction(self):
        text=gold(10)
        spec=interpret({'raw_text':text},CITIES,canned(
            {'f':'attraction','op':'include','v':'北京路步行街','u':None,'s':'local_experience','k':'hard','q':'想去北京路步行街'},
        ))
        self.assertTrue(spec['ready'])
        self.assertEqual(field(spec,'destination_city')['value'],'广州')
        self.assertEqual(field(spec,'attraction')['strength'],'soft')

    def test_rail_series_parentheses_are_not_an_invalid_value(self):
        text=gold(16)
        spec=interpret({'raw_text':text},CITIES,canned(
            {'f':'intercity_mode','op':'select','v':'高铁(G)','u':None,
             's':'round_trip','k':'hard','q':'必须高铁(G)往返'},
        ))
        self.assertTrue(spec['ready'])
        self.assertEqual((field(spec,'intercity_mode')['value'],field(spec,'intercity_mode')['scope']),('高铁G','round_trip'))

    def test_relative_date_without_calendar_context_keeps_original_input(self):
        text=gold(124)
        spec=interpret({'raw_text':text},CITIES,canned(
            {'f':'start_date','op':'eq','v':'明天','u':None,'s':'total_trip','k':'fact','q':'我明天要'},
        ))
        self.assertTrue(spec['ready'])
        self.assertEqual(field(spec,'start_date')['value'],'明天')
        self.assertFalse(spec['issues'])

    def test_fixed_calendar_appointment_requires_a_start_date(self):
        form = {'start_city': '南京', 'target_city': '上海', 'days': 3, 'people_number': 2,
                'appointments': [{'name': '上海博物馆', 'date': '2026-12-03', 'start_time': '15:00',
                                  'duration_minutes': 60}]}
        pending = interpret(form, CITIES)
        self.assertEqual([q['code'] for q in pending['issues']], ['appointment_date_needs_start'])
        dated = interpret({**form, 'start_date': '2026-12-02'}, CITIES)
        self.assertTrue(dated['ready'])
        self.assertEqual(field(dated, 'appointment')['value']['day'], 2)
        mismatched = interpret({**form, 'start_date': '2026-12-04'}, CITIES)
        self.assertEqual([q['code'] for q in mismatched['issues']], ['invalid_appointment_window'])

    def test_no_food_preference_does_not_become_a_missing_hard_field(self):
        text=gold(113)
        spec=interpret({'raw_text':text},CITIES,canned(
            {'f':'cuisine','op':'eq','v':None,'u':None,'s':'total_trip','k':'fact','q':'饮食没有要求'},
        ))
        self.assertTrue(spec['ready'])
        self.assertEqual(field(spec,'cuisine')['operator'],'no_preference')

    def test_rail_alternatives_are_not_two_additional_requirements(self):
        text=gold(126)
        spec=interpret({'raw_text':text},CITIES,canned(
            {'f':'intercity_mode','op':'any_of','v':['高铁','动车'],'u':None,'s':'total_trip',
             'k':'hard','q':'计划坐高铁(G)或者动车(D)去北京'},
            {'f':'intercity_mode','op':'include','v':'G车次','u':None,'s':'total_trip','k':'hard','q':'高铁(G)'},
            {'f':'intercity_mode','op':'include','v':'D车次','u':None,'s':'total_trip','k':'hard','q':'动车(D)'},
        ))
        self.assertTrue(spec['ready'])
        self.assertEqual([(x['value'],x['scope'],x['strength']) for x in spec['items'] if x['field']=='intercity_mode'],
                         [(['高铁G','动车D'],'outbound','soft')])
