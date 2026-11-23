import unittest

from chinatravel.personalization.open_requirements import place_modifiers
from chinatravel.personalization.plan_assessment import _goal_results
from chinatravel.personalization.planning_compat import constructor_input
from chinatravel.personalization.trip_world import _resolve_named_place
from chinatravel.personalization.tripspec import evidence, label_tuple, normalize_saved_spec


def requirement(text, field, value, quote, *, strength='fact', operator='eq'):
    return label_tuple(field,operator,None,'total_trip',strength,value,evidence(text,quote))


class OpenRequirementTests(unittest.TestCase):
    def test_evening_area_requires_evidence_of_place_type_and_time(self):
        text = '想看西湖，晚上逛逛运河一带。'
        item = requirement(text, 'experience_theme', '运河一带', '晚上逛逛运河一带')
        spec = normalize_saved_spec(dict(schema_version='tripspec_v1', items=[item], raw_text=text,
                                         ready=True, issues=[]))
        req = dict(spec['items'][0], id='r1')
        self.assertEqual((req['area_term'],req['preferred_start'],req['strength']),('运河','18:00','soft'))
        snap = dict(destination='杭州', requirements=[req], place_groundings=[],
                    experience_matches={'r1':{'matched_count':1}})
        place = dict(name='京杭大运河杭州景区', id='attraction:1', tags=['文化旅游区'])
        for data, matched in [({'start_time':'14:00'},False),({'start_time':'18:15'},True),
                              ({'name':'运河艺术馆','tags':['美术馆'],'start_time':'19:00'},False)]:
            goal, = _goal_results(snap, [{**place, **data}], {})
            self.assertEqual(goal['status']=='matched',matched)

    def test_place_modifier_is_based_on_scope_and_evidence(self):
        for name, anchor, kind in [('青桐酒店','体育场','hotel'), ('山茶小馆','博物馆','restaurant')]:
            text = f'希望安排{anchor}附近的{name}'
            pin = requirement(text,kind,name,text,strength='hard')
            location = requirement(text,'near_anchor',anchor,anchor+'附近')
            self.assertEqual(place_modifiers([pin, location],text),{'requirement-2':'requirement-1'})
            hard = {**location,'strength':'hard'}
            self.assertEqual(place_modifiers([pin, hard],text),{})
            self.assertEqual(place_modifiers([pin, location],text.replace('附近的','附近，')),{})

    def test_certified_walking_limit_cannot_be_used_as_a_name_hint(self):
        text='住宿必须在体育场步行十分钟以内，优先青桐酒店。'
        hotel = requirement(text,'hotel','青桐酒店','优先青桐酒店',strength='soft')
        anchor = requirement(text,'near_anchor','体育场','住宿必须在体育场步行十分钟以内',strength='hard')
        core = [label_tuple(f,'eq',None,'total_trip','fact',v,{'type':'form'}) for f,v in
                [('origin_city','南京'),('destination_city','武汉'),('days',3),('people',2)]]
        _, unsupported = constructor_input({'items':[*core,hotel,anchor],'raw_text':text})
        self.assertEqual(unsupported,[anchor])

    def test_unique_business_name_resolves_and_multiple_branches_stay_ambiguous(self):
        class Facts:
            def place_with_suggestions(self, *args):
                rows = [{'name':'青桐酒店(体育场店)'}, {'name':'青桐酒店(市中心店)'}][:self.count]
                return {'status':'ambiguous' if self.count>1 else 'unconfirmed_match','records':rows,
                        'matched_count':self.count}
        facts=Facts()
        facts.count=1
        self.assertEqual(_resolve_named_place(facts,'成都','青桐酒店','accommodation',['体育场'])['status'],'found')
        self.assertEqual(_resolve_named_place(facts,'成都','青桐酒店','accommodation',[])['status'],'unconfirmed_match')
        facts.count=2
        self.assertEqual(_resolve_named_place(facts,'成都','青桐酒店','accommodation',['体育场'])['status'],'ambiguous')

    def test_ordinary_core_hard_and_unknown_are_distinct(self):
        for phrase, strength, expected in [
            ('想多逛一些独立书店','fact',('soft','maximize',False)),
            ('此行主要就是为了多逛独立书店','fact',('soft','prioritize',True)),
            ('必须逛至少两家独立书店','hard',('hard','eq',False)),
            ('必须逛至少两家独立书店','soft',('hard','eq',False)),
            ('不想逛独立书店','fact',('fact','eq',False)),
        ]:
            item = requirement(phrase,'experience_example','独立书店',phrase,strength=strength)
            spec = normalize_saved_spec(dict(schema_version='tripspec_v1',items=[item],raw_text=phrase,
                                             ready=True,issues=[]))
            goal, = spec['items']
            self.assertEqual((goal['strength'],goal['operator'],goal.get('primary',False)),expected)

    def test_open_goal_counts_distinct_evidenced_stops_and_reports_gap(self):
        req = dict(id='requirement-1', field='experience_theme',strength='soft', operator='prioritize',
                   value='摄影展', scope='local_experience',source={'type':'original_text'},
                   primary=True,maximize_matches=True,open_experience=True)
        snapshot={'destination':'广州','requirements':[req],'place_groundings':[],
                  'experience_matches':{'requirement-1':{'matched_count':1}}}
        stop={'id':'attraction:guangzhou:12','name':'广州城市摄影展','tags':['艺术馆']}
        goal, = _goal_results(snapshot,[stop,stop],{})
        self.assertEqual((goal['status'],goal['observed']),('matched',1))
        goal, = _goal_results(snapshot,[],{})
        self.assertEqual(goal['reason'],'matching_places_not_scheduled')
        snapshot['experience_matches']={}
        goal, = _goal_results(snapshot,[],{})
        self.assertEqual((goal['status'],goal['reason']),('insufficient_evidence','no_schedulable_catalog_match'))
