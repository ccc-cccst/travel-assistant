"""Analytical and adversarial cases for the independent, model-free scorer."""
from copy import deepcopy
import json
import unittest

from experiments.evaluation.features import score_plan


def activity(name='Museum', start='09:00', end='11:00', kind='attraction', **kwargs):
    return dict(position=name, type=kind, start_time=start, end_time=end,
                transports=kwargs.pop('transports', []), cost=0, **kwargs)


def leg(start='08:30', end='09:00', mode='walk', distance=1., **kwargs):
    return dict(start='Origin', end='Museum', start_time=start, end_time=end,
                mode=mode, distance=distance, cost=0, **kwargs)


def plan(*days):
    return dict(start_city='City', target_city='City', people_number=1,
                itinerary=[dict(day=i + 1, activities=rows) for i, rows in enumerate(days)])


def query(days=1, **kwargs):
    return dict(days=days, start_city=kwargs.pop('start_city', 'City'),
                target_city=kwargs.pop('target_city', 'City'), **kwargs)


def reviewed(*items):
    return dict(reviewed=True, items=list(items))


def preference(feature, mapping, thresholds, aggregation, **kwargs):
    return dict(id=kwargs.pop('id', 'p1'), kind='soft', method='rule', feature=feature,
                mapping=mapping, thresholds=thresholds, aggregation=aggregation,
                missing_policy='unknown', evidence=['User request'], **kwargs)


def semantics(experience=3, preferences=None):
    return dict(status='known', experience=dict(status='known', rating=experience,
                evidence=['d1:a0'], reason='Grounded experience'), preferences=preferences or [])


class RulesTests(unittest.TestCase):
    def test_characterization_rule_score_has_no_time_of_day_suitability(self):
        # A documented limitation, not evidence that daytime/nighttime are equally good.
        morning = score_plan(plan([activity(start='09:00', end='11:00')]), query(), requirements=reviewed())
        evening = score_plan(plan([activity(start='19:00', end='21:00')]), query(), requirements=reviewed())
        self.assertEqual(morning['quality_score'], evening['quality_score'])
        self.assertIsNotNone(morning['quality_score'])

    def test_hand_computed_local_day_and_fixed_denominator(self):
        p = plan([activity(transports=[leg()]), activity(start='11:00', end='12:00', kind='lunch')])
        r = score_plan(p, query(), requirements=reviewed(), config={'activity_window': ['08:00', '18:00']})
        self.assertEqual(r['metrics']['available_minutes'], 600)
        self.assertEqual(r['metrics']['days'][0]['busy_minutes'], 210)
        self.assertAlmostEqual(r['components']['T']['score'], 95)
        self.assertAlmostEqual(r['components']['R']['score'], 87.5)
        self.assertEqual(r['components']['H']['status'], 'not_applicable')
        self.assertEqual(r['components']['U']['score'], 100)
        self.assertAlmostEqual(r['quality_score'], (95 * .3 + 87.5 * .25 + 100 * .1) / .65)
        self.assertEqual(r['composite_score'], r['quality_score'])
        less = score_plan(plan([activity()]), query(), config={'activity_window': ['08:00', '18:00']})
        self.assertEqual(less['metrics']['available_minutes'], 600)

    def test_missing_transport_is_unknown_not_zero(self):
        a = activity()
        del a['transports']
        r = score_plan(plan([a]), query(), requirements=reviewed())
        self.assertIsNone(r['metrics']['transport_minutes'])
        self.assertEqual(r['metrics']['known_transport_minutes'], 0)
        self.assertIsNone(r['quality_score'])
        self.assertIsNone(r['composite_score'])
        self.assertLess(r['coverage']['quality']['fraction'], 1)

    def test_empty_route_between_different_positions_is_unknown(self):
        r = score_plan(plan([activity(), activity('Park', '12:00', '13:00')]), query())
        self.assertEqual(r['components']['T']['status'], 'unknown')
        self.assertTrue(any(i['code'] == 'missing_movement' for i in r['issues']))

    def test_missing_distance_does_not_erase_known_time(self):
        route = leg()
        del route['distance']
        r = score_plan(plan([activity(transports=[route])]), query())
        self.assertEqual(r['metrics']['transport_minutes'], 30)
        self.assertIsNone(r['metrics']['transport_distance_km'])
        self.assertIsNone(r['metrics']['walking_distance_km'])
        self.assertEqual(r['components']['T']['status'], 'known')

    def test_busy_union_intersection_and_outside_window(self):
        p = plan([activity(start='07:00', end='10:00', transports=[leg('06:30', '07:00')]),
                  activity(start='09:00', end='11:00', kind='lunch')])
        r = score_plan(p, query(), config={'activity_window': ['08:00', '12:00'],
                                         'locked_intervals': [{'day': 1, 'start_time': '09:00', 'end_time': '10:00'}]})
        day = r['metrics']['days'][0]
        self.assertEqual(day['available_minutes'], 180)
        self.assertEqual(day['busy_minutes'], 120)
        self.assertEqual(day['outside_window_busy_minutes'], 150)
        self.assertEqual(r['metrics']['transport_minutes'], 30)
        self.assertLessEqual(day['busy_ratio'], 1)

    def test_partial_arrival_and_departure_days_exclude_access(self):
        go = dict(type='train', start='Origin', end='CityStation', start_time='09:00', end_time='12:00', transports=[], cost=0)
        back = dict(type='train', start='CityStation', end='Origin', start_time='10:00', end_time='13:00',
                    transports=[leg('09:00', '10:00')], cost=0)
        p = plan([go, activity(start='13:00', end='15:00', transports=[leg('12:00', '13:00')])], [back])
        r = score_plan(p, query(2, start_city='Origin'))
        self.assertEqual([d['available_minutes'] for d in r['metrics']['days']], [480, 60])
        self.assertEqual(r['metrics']['available_minutes'], 540)
        self.assertEqual(r['metrics']['transport_minutes'], 120)

    def test_no_tickets_in_nonlocal_trip_needs_explicit_bounds(self):
        p = plan([activity()])
        q = query(start_city='Origin')
        self.assertIsNone(score_plan(p, q)['quality_score'])
        r = score_plan(p, q, config={'trip_bounds': {'start_day': 1, 'start_time': '08:00', 'end_day': 1, 'end_time': '18:00'}})
        self.assertEqual(r['metrics']['available_minutes'], 600)

    def test_locked_day_is_excluded_from_rhythm_denominator(self):
        p = plan([activity()], [activity()])
        r = score_plan(p, query(2), config={'locked_intervals': [{'day': 1, 'start_time': '00:00', 'end_time': '24:00'}]})
        self.assertEqual(r['metrics']['days'][0]['available_minutes'], 0)
        self.assertEqual(r['components']['R']['denominator'], 780)

    def test_zero_availability_and_all_na(self):
        p = plan([])
        r = score_plan(p, query(), config={'available_intervals': {}}, requirements=reviewed())
        self.assertEqual(r['components']['T']['status'], 'unknown')
        self.assertIsNone(r['quality_score'])
        na = score_plan(p, query(), requirements=reviewed(), config={'applicability': {k: False for k in 'TRHUE'}})
        self.assertEqual(na['quality_status'], 'not_applicable')
        self.assertEqual(na['composite_status'], 'not_applicable')
        self.assertIsNone(na['coverage']['quality']['fraction'])

    def test_zero_weights_do_not_divide_or_create_full_score(self):
        r = score_plan(plan([activity()]), query(), config={'quality_weights': {k: 0 for k in 'TRHUE'}})
        self.assertEqual(r['quality_status'], 'not_applicable')
        self.assertIsNone(r['quality_score'])

    def test_empty_attractions_score_zero_not_na(self):
        r = score_plan(plan([activity(kind='lunch')]), query())
        self.assertEqual(r['components']['U']['score'], 0)
        self.assertEqual(r['components']['U']['status'], 'known')

    def test_missing_plan_data_preserves_unknown(self):
        r = score_plan({}, query(), requirements=reviewed())
        self.assertIsNone(r['quality_score'])
        self.assertIsNone(r['metrics']['transport_minutes'])
        self.assertIsNone(r['metrics']['total_cost'])

    def test_contiguous_fragments_are_one_visit_and_allowances_are_bounded(self):
        p = plan([activity(start='09:00', end='10:00'), activity(start='10:00', end='11:00'),
                  activity(start='12:00', end='13:00'), activity(start='15:00', end='16:00')])
        r = score_plan(p, query(), config={'necessary_revisits': ['Museum']})
        self.assertEqual(r['metrics']['attraction_visits'], 3)
        self.assertEqual(r['metrics']['merged_visit_segments'], 1)
        self.assertEqual(r['metrics']['nonessential_revisits'], 1)
        self.assertAlmostEqual(r['components']['U']['score'], 100 * 2 / 3)

    def test_same_city_switch_and_required_switch_exemption(self):
        p = plan([activity('A', '20:00', '24:00', 'hotel')],
                 [activity('B', '20:00', '24:00', 'hotel')],
                 [activity('B', '08:00', '09:00')])
        r = score_plan(p, query(3))
        self.assertEqual(r['metrics']['overnight_stays'], 2)
        self.assertEqual(r['components']['H']['score'], 50)
        exempt = score_plan(p, query(3), config={'necessary_hotel_switches': [{'from': 'A', 'to': 'B'}]})
        self.assertEqual(exempt['components']['H']['score'], 100)

    def test_unknown_hotel_city_does_not_mean_different_city(self):
        p = plan([activity('A', '20:00', '24:00', 'hotel')], [activity('B', '20:00', '24:00', 'hotel')])
        q = query(2, target_city=['A', 'B'])
        r = score_plan(p, q)
        self.assertEqual(r['components']['H']['status'], 'unknown')
        known = score_plan(p, q, resources={'hotels': {'A': {'city': 'X'}, 'B': {'city': 'Y'}}})
        self.assertEqual(known['components']['H']['score'], 100)

    def test_one_overnight_checkout_is_one_night(self):
        r = score_plan(plan([activity('A', '20:00', '08:00', 'hotel', end_day_offset=1)], []), query(2))
        self.assertEqual(r['metrics']['overnight_stays'], 1)

    def test_missing_required_hotel_not_removed_from_quality(self):
        r = score_plan(plan([activity()], [activity()]), query(2))
        self.assertEqual(r['components']['H']['score'], 0)
        self.assertTrue(r['components']['H']['applicable'])

    def test_midnight_local_route_aligned_before_activity(self):
        r = score_plan(plan([], [activity(start='00:30', end='01:00', transports=[leg('23:30', '00:30')])]), query(2),
                       config={'activity_window': ['22:00', '02:00'], 'applicability': {'H': False}})
        self.assertEqual(r['metrics']['transport_minutes'], 60)
        self.assertEqual([d['available_minutes'] for d in r['metrics']['days']], [120, 240])
        self.assertEqual([d['transport_minutes'] for d in r['metrics']['days']], [30, 30])

    def test_overnight_ticket_requires_explicit_day_or_duration(self):
        go = dict(type='train', start_time='23:00', end_time='06:00', transports=[], cost=0)
        back = dict(type='train', start_time='18:00', end_time='21:00', transports=[], cost=0)
        p = plan([go], [back])
        self.assertIsNone(score_plan(p, query(2, start_city='Origin'))['metrics']['available_minutes'])
        go['end_day_offset'] = 1
        self.assertEqual(score_plan(p, query(2, start_city='Origin'))['metrics']['available_minutes'], 600)

    def test_endpoint_pace_targets_and_overnight_overlap_safe(self):
        r = score_plan(plan([activity(start='00:00', end='24:00')]), query(), config={'pace_target': [0, 1]})
        self.assertEqual(r['components']['R']['score'], 100)
        r = score_plan(plan([], []), query(2), config={'day_windows': {'1': [['22:00', '02:00']], '2': [['00:00', '04:00']]}})
        self.assertEqual(r['metrics']['available_minutes'], 360)


class PreferenceTests(unittest.TestCase):
    def test_unreviewed_is_not_equivalent_to_no_preferences(self):
        p = plan([activity()])
        for req in (None, {'reviewed': False, 'items': []}):
            r = score_plan(p, query(), requirements=req)
            self.assertEqual(r['preference_status'], 'unknown')
            self.assertIsNone(r['composite_score'])
        r = score_plan(p, query(), requirements=reviewed())
        self.assertEqual(r['preference_status'], 'not_applicable')
        self.assertEqual(r['composite_score'], r['quality_score'])

    def test_unique_attraction_tag_fraction_unknown_and_empty_tags(self):
        req = reviewed(preference('attraction_tags', 'tag_fraction', {'any_of': ['culture']}, 'unique_attractions'))
        p = plan([activity(), activity(start='13:00', end='14:00')])
        r = score_plan(p, query(), requirements=req, resources={'attractions': {'Museum': {'tags': ['culture']}}})
        self.assertEqual(r['preference_score'], 100)
        unknown = score_plan(p, query(), requirements=req)
        self.assertIsNone(unknown['preference_score'])
        known_empty = score_plan(p, query(), requirements=req, resources={'attractions': {'Museum': {'tags': []}}})
        self.assertEqual(known_empty['preference_score'], 0)

    def test_pace_preference_replaces_generic_r_target(self):
        req = reviewed(preference('pace', 'pace', {'lower': .1, 'upper': .3}, 'available_minutes'))
        r = score_plan(plan([activity()]), query(), requirements=req)
        self.assertEqual(r['components']['R']['score'], 100)
        self.assertEqual(r['preference_score'], 100)

    def test_physical_effort_equal_day_weight_and_one_sided_tolerance(self):
        req = reviewed(preference('walking_minutes', 'upper_bound', {'target': 20, 'scale': 40}, 'day_equal'))
        p = plan([activity(transports=[leg('08:00', '09:00')])], [activity()])
        r = score_plan(p, query(2), requirements=req)
        self.assertEqual(r['preference_score'], 50)
        invalid = deepcopy(req)
        invalid['items'][0]['thresholds']['scale'] = 0
        bad = score_plan(p, query(2), requirements=invalid)
        self.assertIsNone(bad['preference_score'])
        self.assertEqual(bad['preferences'][0]['status'], 'unconfigured')

    def test_known_preference_is_retained_but_unknown_not_dropped(self):
        known = preference('transport_minutes', 'upper_bound', {'target': 40, 'scale': 10}, 'total', id='known', weight=2)
        unknown = preference('attraction_tags', 'tag_fraction', {'any_of': ['nature']}, 'unique_attractions', id='unknown')
        r = score_plan(plan([activity()]), query(), requirements=reviewed(known, unknown))
        self.assertEqual(r['preference_status'], 'partial')
        self.assertIsNone(r['preference_score'])
        self.assertIsNone(r['composite_score'])
        self.assertAlmostEqual(r['coverage']['preferences']['fraction'], 2 / 3)

    def test_night_weighted_hotel_and_field_weighted_facilities(self):
        p = plan([activity('A', '20:00', '24:00', 'hotel', nights=2)],
                 [activity('B', '20:00', '24:00', 'hotel')], [])
        resources = {'hotels': {'A': {'stars': 4, 'facilities': ['wifi', 'pool']}, 'B': {'stars': 2, 'facilities': ['wifi']}}}
        req = reviewed(preference('hotel_stars', 'binary', {'equals': 4}, 'night_weighted'))
        r = score_plan(p, query(3), resources=resources, requirements=req)
        self.assertAlmostEqual(r['preference_score'], 200 / 3)
        req = reviewed(preference('hotel_facilities', 'binary', {'all_of': ['wifi', 'pool']}, 'night_weighted'))
        r = score_plan(p, query(3), resources=resources, requirements=req)
        self.assertAlmostEqual(r['preference_score'], 250 / 3)

    def test_two_sided_mapping_and_unknown_intensity(self):
        req = reviewed(preference('activity_intensity', 'interval', {'lower': 1, 'upper': 3, 'lower_scale': 1, 'upper_scale': 2}, 'day_equal'))
        p = plan([activity()])
        self.assertIsNone(score_plan(p, query(), requirements=req)['preference_score'])
        r = score_plan(p, query(), requirements=req, resources={'attractions': {'Museum': {'intensity': 4}}})
        self.assertEqual(r['preference_score'], 50)

    def test_missing_frozen_mapping_or_evidence_is_unconfigured(self):
        item = preference('walking_minutes', 'upper_bound', {'target': 30, 'scale': 10}, 'day_equal')
        del item['aggregation']
        r = score_plan(plan([activity()]), query(), requirements=reviewed(item))
        self.assertEqual(r['preferences'][0]['status'], 'unconfigured')
        self.assertIsNone(r['composite_score'])

    def test_full_semantic_rating_and_fast_partial(self):
        item = dict(id='theme', kind='soft', method='semantic', evidence=['User: theme'], weight=1)
        sem = semantics(3, [dict(id='theme', status='known', rating=2, evidence=['d1:a0'])])
        p = plan([activity()])
        full = score_plan(p, query(), profile='full_v0', requirements=reviewed(item), semantic=sem)
        self.assertEqual(full['components']['E']['score'], 75)
        self.assertEqual(full['preference_score'], 50)
        self.assertAlmostEqual(full['composite_score'], .5 * (full['quality_score'] + 50))
        fast = score_plan(p, query(), requirements=reviewed(item), semantic=sem)
        self.assertIsNone(fast['preference_score'])
        self.assertEqual(fast['components']['E']['status'], 'not_applicable')

    def test_full_missing_evidence_is_unknown_not_zero_or_renormalized(self):
        p = plan([activity()])
        sem = semantics(4)
        sem['experience']['evidence'] = []
        full = score_plan(p, query(), profile='full_v0', semantic=sem, requirements=reviewed())
        self.assertIsNone(full['quality_score'])
        self.assertEqual(full['components']['T']['status'], 'known')
        na = score_plan(p, query(), profile='full_v0', config={'applicability': {'E': False}}, requirements=reviewed())
        self.assertIsNotNone(na['quality_score'])

    def test_partial_semantic_result_preserves_known_experience_and_preference(self):
        a = dict(id='a', kind='soft', method='semantic', evidence=['User a'])
        b = dict(id='b', kind='soft', method='semantic', evidence=['User b'])
        sem = semantics(3, [dict(id='a', status='known', rating=4, evidence=['d1:a0']),
                            dict(id='b', status='unknown', rating=None, evidence=[])])
        sem['status'] = 'unknown'
        r = score_plan(plan([activity()]), query(), profile='full_v0', requirements=reviewed(a, b), semantic=sem)
        self.assertEqual(r['components']['E']['score'], 75)
        self.assertIsNotNone(r['quality_score'])
        self.assertIsNone(r['preference_score'])
        self.assertEqual(r['preference_status'], 'partial')
        self.assertEqual(r['coverage']['preferences']['fraction'], .5)

    def test_conflicting_pace_targets_are_not_last_item_wins(self):
        a = preference('pace', 'pace', {'lower': .1, 'upper': .3}, 'available_minutes', id='a')
        b = preference('pace', 'pace', {'lower': .6, 'upper': .9}, 'available_minutes', id='b')
        r = score_plan(plan([activity()]), query(), requirements=reviewed(a, b))
        self.assertEqual(r['components']['R']['status'], 'unknown')
        self.assertIsNone(r['composite_score'])

    def test_malformed_reviewed_requirements_remain_unknown(self):
        r = score_plan(plan([activity()]), query(), requirements={'reviewed': True, 'items': None})
        self.assertEqual(r['preference_status'], 'unknown')
        self.assertIsNone(r['composite_score'])

    def test_inputs_unchanged_and_json_has_no_nan(self):
        p, q = plan([activity()]), query()
        req, cfg, sem = reviewed(), {'pace_target': [0, 1]}, semantics()
        before = deepcopy([p, q, req, cfg, sem])
        result = score_plan(p, q, requirements=req, config=cfg, semantic=sem)
        self.assertEqual([p, q, req, cfg, sem], before)
        json.dumps(result, allow_nan=False)

    def test_invalid_configuration_rejected(self):
        for cfg in ({'quality_weights': {'T': -1}}, {'pace_target': [.9, .2]}, {'required_nights': -1}, {'applicability': {'T': None}}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                score_plan(plan([activity()]), query(), config=cfg)


if __name__ == '__main__':
    unittest.main()
