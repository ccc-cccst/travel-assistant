import unittest

from chinatravel.personalization.request import normalize_request, fingerprint
from chinatravel.personalization.catalog import PreferenceCatalog
from chinatravel.personalization.compiler import compile_request
from chinatravel.personalization.evaluation import evaluate_preferences
from chinatravel.personalization.policy import PreferencePolicy


class PersonalizationTests(unittest.TestCase):
    def base(self, **extra):
        return dict(uid='test', start_city='南京', target_city='上海', days=6,
                    people_number=2, nature_language='', **extra)

    def test_longer_trip_and_missing_interests(self):
        request = normalize_request(self.base())
        self.assertEqual(request['trip']['days'], 6)
        self.assertEqual(request['preferences'], [])

    def test_negative_full_pace_is_relaxed(self):
        value=self.base(); value['nature_language']='不要特种兵出行'
        p=normalize_request(value)['preferences']
        self.assertIn('relaxed',[i['target'] for i in p])
        self.assertNotIn('full',[i['target'] for i in p])

    def test_existing_preference_variants_recognized(self):
        for text,target in [('希望尽量减少住宿费用','lodging_cost'),('我希望在餐饮上花费尽可能多的预算','food_share'),
                            ('我偏好去餐厅的交通时间尽可能少','near_restaurants')]:
            value=self.base(); value['nature_language']=text
            self.assertIn(target,[p['target'] for p in normalize_request(value)['preferences']])

    def test_directional_train_does_not_force_return(self):
        value=self.base(); value['nature_language']='坐火车去'
        r=normalize_request(value)
        self.assertEqual(r['directional_modes'],{'outbound':['train']})
        self.assertEqual(set(r['facts']['intercity_modes']),{'train','airplane'})

    def test_no_reference_leaks(self):
        value = self.base(hard_logic_py=['SECRET_ORACLE'], preference_py='SECRET_PREFERENCE')
        request = normalize_request(value)
        self.assertNotIn('SECRET_', str(request))
        self.assertNotIn('SECRET_', str(compile_request(request)))

    def test_cache_changes_with_profile(self):
        a = normalize_request(self.base(profile={'interests':['museum']}))
        b = normalize_request(self.base(profile={'interests':['nature']}))
        self.assertNotEqual(fingerprint(a), fingerprint(b))

    def test_form_text_conflict(self):
        value = self.base(budget=1500)
        value['nature_language']='总预算最多3000元'
        request = normalize_request(value)
        self.assertTrue(any(i['blocking'] for i in request['issues']))

    def test_soft_not_hard_and_unsupported_preserved(self):
        value=self.base(); value['nature_language']='喜欢自然风光，不要太累，希望无障碍，养生为主'
        request=normalize_request(value)
        self.assertTrue(any(p['dimension']=='interest' for p in request['preferences']))
        bundle=compile_request(request, allow_issues=True)
        self.assertFalse(any('自然风光' in x for x in bundle['legacy_query']['hard_logic']))
        self.assertTrue(request['unmapped_requirements'])

    def test_bed_count_does_not_invent_rooms(self):
        value=self.base(); value['nature_language']='住大床房'
        request=normalize_request(value)
        self.assertEqual(request['facts']['room_type'],1)
        self.assertIsNone(request['facts']['rooms'])

    def test_appointment_and_daily_limit_preserved(self):
        value=self.base(appointments=[{'name':'测试馆','day':5,'start_time':'15:00','duration_minutes':60}],
                        max_daily_access_walk_minutes=30)
        request=normalize_request(value)
        bundle=compile_request(request)
        self.assertEqual(bundle['legacy_query']['appointments'][0]['day'],5)
        self.assertEqual(request['hard_limits']['max_daily_access_walk_minutes'],30)

    def catalog(self):
        return PreferenceCatalog.from_records('上海', attractions=[
            {'name':'公园','type':'公园'}, {'name':'测试馆','type':'博物馆/纪念馆'}],
            restaurants=[{'name':'饭店','cuisine':'本帮菜'}], accommodations=[])

    def plan(self, name, price=100):
        return {'start_city':'南京','target_city':'上海','people_number':2,
                'itinerary':[{'day':1,'activities':[{'type':'attraction','position':name,
                'start_time':'10:00','end_time':'12:00','cost':price,'transports':[]}]}]}

    def test_preference_switch_and_unknown(self):
        c=self.catalog()
        r=normalize_request(self.base(profile={'interests':['museum']}))
        a=evaluate_preferences(self.plan('公园'),r,c)
        b=evaluate_preferences(self.plan('测试馆'),r,c)
        self.assertGreater(b['utility'],a['utility'])
        unknown=evaluate_preferences(self.plan('未知馆'),r,c)
        self.assertEqual(unknown['items'][0]['status'],'unknown')

    def test_recall_before_cutoff_keeps_interest_and_must(self):
        import pandas as pd
        frame=pd.DataFrame([{'name':'公园','type':'公园'}]*15+[{'name':'测试馆','type':'博物馆/纪念馆'}])
        policy=PreferencePolicy(normalize_request(self.base(profile={'interests':['museum']})),self.catalog())
        recalled=policy.recall(frame,list(range(16)),required={0},limit=4)
        self.assertIn(0,recalled)
        self.assertIn(15,recalled)

    def test_hard_missing_feature_rejects_unknown(self):
        r=normalize_request(self.base(profile={'hotel_features':['泳池'],'required_hotel_features':['泳池']}))
        result=evaluate_preferences(self.plan('测试馆'),r,self.catalog())
        self.assertFalse(result['eligible'])

    def test_overlapping_appointments_are_blocking(self):
        r=normalize_request(self.base(appointments=[
            {'name':'甲馆','day':2,'start_time':'15:00','duration_minutes':60},
            {'name':'乙馆','day':2,'start_time':'15:30','duration_minutes':60}]))
        self.assertFalse(r['ready'])

    def test_actual_selection_key_is_used(self):
        from chinatravel.soft_constraints.pool import CandidatePool
        pool=CandidatePool(3)
        pool.items=[{'ordinal':1,'fingerprint':'base','score':{'total_penalty':1,'selection_key':[10]}},
                    {'ordinal':2,'fingerprint':'personal','score':{'total_penalty':2,'selection_key':[3]}}]
        self.assertEqual(pool.best()['fingerprint'],'personal')

    def test_appointment_cannot_pass_if_removed(self):
        r=normalize_request(self.base(appointments=[{'name':'测试馆','day':1,'start_time':'15:00','duration_minutes':60}]))
        report=evaluate_preferences(self.plan('测试馆'),r,self.catalog())
        self.assertFalse(report['eligible'])
        self.assertTrue(any(v['field']=='appointment' for v in report['violations']))

    def test_grounded_model_cannot_add_hard_or_invented_source(self):
        from chinatravel.personalization.model import interpret_preferences
        import json
        value=self.base(); value['nature_language']='希望少折返，必须去博物馆'
        r=normalize_request(value)
        def model(*args,**kwargs): return json.dumps({'items':[
            {'dimension':'interest','target':'museum','source':'必须去博物馆'},
            {'dimension':'mobility','target':'less_transit','source':'希望少折返'},
            {'dimension':'pace','target':'relaxed','source':'我想舒缓'}]})
        got=interpret_preferences(r,self.catalog(),model)
        self.assertEqual(got['model_interpretation']['rejected'],2)
        self.assertTrue(any(p['target']=='less_transit' for p in got['preferences']))


if __name__=='__main__': unittest.main()
