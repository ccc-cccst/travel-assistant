import unittest

from chinatravel.personalization.cli import ROOT, read
from chinatravel.personalization.planning_compat import constructor_input
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.trip_world import _candidate_allowed, theme_name_matches
from chinatravel.personalization.tripspec import normalize_saved_spec, resolve_conflicts
from chinatravel.planning.snapshot_constraints import required_features_present


class DeliveryTests(unittest.TestCase):
    def saved(self,case):
        return read(ROOT/'result/experiments/P034_delivery_and_quality/web_live_01'/case/'request.json')

    def test_hotel_feature_disjunction_is_exact_and_each_required_facility_applies(self):
        opts={'影音房':('自营影音房','影音房')}
        request,unsupported=constructor_input(self.saved('easy_145'))
        self.assertFalse(unsupported)
        self.assertCountEqual(request['hotel_feature_options']['影音房'],opts['影音房'])
        self.assertTrue(required_features_present('自营影音房、健身室',['影音房','健身室'],opts))
        self.assertFalse(required_features_present('附近健身室、影音房',['健身室','影音房'],opts))
        record={'kind':'accommodation','planning_catalog':True,'routable_in_snapshot':True,
                'tags':{'values':['自营影音房']},'price':{'usable_for_snapshot_budget':True}}
        self.assertTrue(_candidate_allowed(record,{'required_features':{'影音房'}}))
        self.assertFalse(_candidate_allowed(record,{'required_features':{'影音房','健身室'}}))

    def test_confirm_date_keeps_party_flight_and_intended_visits(self):
        spec=normalize_saved_spec(self.saved('human_122'))
        date_issue,=spec['issues']
        with self.assertRaises(ValueError):
            resolve_conflicts(spec,{date_issue['id']:'2027-02-30'})
        fixed=normalize_saved_spec(resolve_conflicts(spec,{date_issue['id']:'2027-09-15'}))
        self.assertTrue(fixed['ready'])
        self.assertEqual([(i['value'],i['scope']) for i in fixed['items'] if i['field']=='intercity_mode'],
                         [('飞机','outbound')])
        self.assertEqual({i['value'] for i in fixed['items'] if i['field']=='experience_theme' and i['strength']=='soft'},
                         {'海洋馆','动物园'})
        self.assertTrue(theme_name_matches('广州动物园',{'动物园'}))
        self.assertFalse(theme_name_matches('广州塔',{'动物园'}))

    def test_ambiguous_area_requires_consent(self):
        spec=normalize_saved_spec(self.saved('human_143'))
        self.assertFalse(spec['ready'])
        question,=spec['issues']
        self.assertEqual(question['code'],'area_scope_confirmation')
        fixed=normalize_saved_spec(resolve_conflicts(spec,{question['id']:0}))
        self.assertTrue(fixed['ready'])
        self.assertEqual(fixed,normalize_saved_spec(fixed))

    def test_deferred_regional_alternative_keeps_appointment_on_original_day(self):
        world=read(ROOT/'result/experiments/P010_trip_world_snapshot/run_02/four_day_appointment/WORLD_FACTS.json')
        drafts=propose_trip_sketches(world)
        lock,=drafts['fixed_appointments']
        regional=drafts['regional_alternative']
        self.assertEqual([day['day'] for day in regional['days'] if lock['id'] in day['core_ids']],[lock['day']])
        self.assertFalse(any(lock['id'] in day['alternative_ids'] for day in regional['days']))
