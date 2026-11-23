from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from chinatravel.personalization import integrated_planning, sketch_planning
from chinatravel.personalization.cli import ROOT, read, save
from chinatravel.personalization.integrated_planning import display_days, run_trip_plan
from chinatravel.personalization.plan_assessment import _hard_checks
from chinatravel.personalization.planning_compat import constructor_input
from chinatravel.personalization.sketch_planning import optimize_day
from chinatravel.personalization.tripspec import normalize_saved_spec


FROZEN = ROOT / 'result/experiments/P014_integrated_planning/frozen30_01'
P024 = ROOT / 'result/experiments/P024_sealed_planning_acceptance/live_02'


class IntegratedPlanningTests(unittest.TestCase):
    def test_late_candidate_preserves_first_and_baseline_recommendation_at_capacity(self):
        def assessed(identifier, cost, transit, visit):
            return {'id': identifier, 'goals': [], 'validation_report': {'passed': True},
                    'quality_vector': {'total_cost_cny': cost, 'city_transit_minutes': transit,
                                       'access_walk_minutes': 10, 'visit_minutes': visit}}

        original = [assessed('first', 1400, 130, 180), assessed('best', 1100, 40, 180),
                    assessed('unused', 1600, 100, 90)]
        archive = {'archive': [{'id': item['id']} for item in original]}
        assessment = {'candidates': original, 'selection': integrated_planning.select_candidates(original)}
        extra = assessed('different', 3600, 15, 300)
        self.assertEqual(integrated_planning.select_candidates([*original, extra])['recommended_id'], 'different')
        include = integrated_planning._include_trial_candidate

        self.assertEqual(include(archive, assessment, {'id': 'different'}, extra, 3), 'added')
        self.assertEqual([item['id'] for item in archive['archive']], ['first', 'best', 'different'])
        self.assertEqual(assessment['selection']['recommended_id'], 'best')
        self.assertEqual([item['id'] for item in assessment['selection']['alternatives']], ['different'])
        self.assertEqual(assessment['verified_count'], 3)

    def test_general_context_does_not_disable_hard_constraints(self):
        root = ROOT / 'result/experiments/P029_generalization'
        for name, field in (
            ('paired_01/confirmed/human1000_541', 'dish'),
            ('paired_01/independent/medium_122', 'experience_example'),
            ('related_01/human_60', 'date_scope'),
        ):
            with self.subTest(field=field):
                spec = read(root / name / 'on/request.json')
                item = next(i for i in spec['items'] if i['field'] == field)
                self.assertFalse(constructor_input(spec)[1])
                item['strength'] = 'hard'
                self.assertIn(item, constructor_input(spec)[1])
                if field == 'date_scope':
                    item['strength'] = 'fact'
                    spec['items'].append({**item, 'field': 'start_date', 'value': '2026-11-23'})
                    self.assertIn(item, constructor_input(spec)[1])
                    spec['items'][-1]['value'] = '2026-11-21'
                    self.assertFalse(constructor_input(spec)[1])
                    spec['items'][-1]['value'] = '2026-11-22'
                    self.assertIn(item, constructor_input(spec)[1])

    def test_same_day_rejection_requires_complete_consistent_evidence(self):
        folder = ROOT / 'result/experiments/P029_generalization/paired_01/independent/easy_24/on'
        snapshot = read(folder / 'WORLD_FACTS.json')
        check = integrated_planning.same_day_conflict
        self.assertEqual(check(snapshot), {'earliest_arrival': '15:46', 'latest_departure': '13:18'})
        group = snapshot['intercity']['return'][0]
        record = group['records'][0]
        old = record.copy()
        record.update(departure='18:00', arrival='20:00', duration_minutes=120)
        self.assertIsNone(check(snapshot))
        record.update(old, duration_minutes=None)
        self.assertIsNone(check(snapshot))
        record.update(old)
        group['records'].pop()
        self.assertIsNone(check(snapshot))

    def test_status_writer_recovers_from_a_short_windows_sharing_conflict(self):
        replace = Path.replace
        attempts = []

        def locked_once(source, destination):
            attempts.append(source)
            if len(attempts) == 1:
                error = PermissionError('temporarily locked by a status reader')
                error.winerror = 5
                raise error
            return replace(source, destination)

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'STATUS.json'
            save(path, {'state': 'planning'})
            with patch.object(Path, 'replace', new=locked_once):
                save(path, {'state': 'complete'})
            self.assertEqual(read(path), {'state': 'complete'})
            self.assertEqual(len(attempts), 2)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_default_cards_hide_ordinary_times_and_keep_the_appointment(self):
        folder = ROOT / 'result/experiments/P012_sketch_planning/run_02/four_day_appointment'
        spec = read(folder / 'request.json')
        plan = read(folder / 'FIRST_CANDIDATE.json')['itinerary']
        days = display_days(plan, spec)
        locks = [(d['day'], a['name'], a['time']) for d in days for a in d['activities'] if a.get('locked')]
        self.assertEqual(locks, [(3, '上海博物馆', '15:00–16:00')])
        self.assertTrue(all('time' not in a for d in days for a in d['activities']
                            if a['kind'] not in ('train', 'airplane') and not a.get('locked')))
        self.assertTrue(all('incoming_minutes' in a for d in display_days(plan, spec, ordered=True)
                            for a in d['activities']))

    def test_frozen_local_transport_requirements_reach_the_constructor(self):
        for case, modes in (('easy_279', ('metro', 'walk')), ('medium_141', ('taxi',)),
                            ('medium_104', ('walk',)), ('medium_106', ('taxi',))):
            with self.subTest(case=case):
                request, unsupported = constructor_input(read(FROZEN / case / 'request.json'))
                self.assertEqual(request['local_modes'], modes)
                self.assertFalse(unsupported)

    def test_transport_exclusion_intersects_with_selection_and_unknown_modes_stay_blocking(self):
        spec = read(FROZEN / 'easy_279/request.json')
        metro = next(i for i in spec['items'] if i['field'] == 'local_mode')
        spec['items'].append({**metro, 'operator': 'exclude', 'value': '出租车'})
        self.assertEqual(constructor_input(spec)[0]['local_modes'], ('metro', 'walk'))
        spec['items'].append({**metro, 'operator': 'exclude', 'value': ['地铁', '步行']})
        self.assertEqual(constructor_input(spec)[0]['local_modes'], ())
        metro['value'] = ['地铁', '公交车']
        self.assertEqual(constructor_input(spec)[1][0]['value'], ['地铁', '公交车'])

    def test_local_transport_conflict_is_reported_before_loading_the_world(self):
        spec = read(FROZEN / 'easy_279/request.json')
        metro = next(i for i in spec['items'] if i['field'] == 'local_mode')
        spec['items'].append({**metro, 'operator': 'exclude', 'value': ['地铁', '步行']})
        with patch.object(integrated_planning, 'save', side_effect=lambda _, payload: payload):
            result = run_trip_plan(spec, Path('unused'), env=None, facts=None)
        self.assertEqual(result['state'], 'local_mode_conflict')
        self.assertEqual(result['stage'], 'input')
        self.assertEqual(len(result['evidence']['conflicting_requirements']), 2)

    def test_a_taxi_leg_in_a_metro_itinerary_fails_the_hard_check(self):
        spec = read(FROZEN / 'easy_279/request.json')
        local = next(i for i in spec['items'] if i['field'] == 'local_mode')
        legs = [{'mode': mode, 'start': f'地点 {i}', 'end': f'地点 {i + 1}'}
                for i, mode in enumerate(('walk', 'metro', 'walk'))]
        plan = {'itinerary': [{'day': 1, 'activities': [{'transports': legs}]}]}
        report = {'issues': [], 'snapshot_evidence': {'tickets': []}}
        requirement = {'id': 'local-1', **local}
        checked, = _hard_checks([requirement], [], plan, report, 3)
        self.assertEqual(checked['status'], 'passed')
        legs[1]['mode'] = 'taxi'
        checked, = _hard_checks([requirement], [], plan, report, 3)
        self.assertEqual(checked['status'], 'violated')
        self.assertEqual(checked['observed']['violations'], [
            {'day': 1, 'from': '地点 1', 'to': '地点 2', 'mode': 'taxi'},
        ])

    def test_no_food_preference_is_not_a_planning_constraint(self):
        request, unsupported = constructor_input(read(FROZEN / 'human_85/request.json'))
        self.assertFalse(unsupported)
        self.assertFalse(request['profile']['restaurant_types'])

    def test_coverage_cases_reach_planning_without_dropping_lodging_requirements(self):
        cases = {
            'easy_124': ('rooms', 1),
            'human_2': ('budget', 4000),
            'human_116': ('requires_lodging', True),
            'easy_168': ('required_hotel_queries', ['丽呈東谷酒店(深圳华强北店)']),
            'human_84': ('hotel_party_price_limit', 300),
            'easy_139': ('required_hotel_features', ['棋牌室']),
            'medium_41': ('rooms', 1),
            'medium_18': ('rooms', 1),
        }
        for case, (key, value) in cases.items():
            with self.subTest(case=case):
                request, unsupported = constructor_input(read(P024 / case / 'program/request.json'))
                self.assertFalse(unsupported)
                self.assertEqual(request[key], value)
                self.assertNotIn('room_type', request)

    def test_archived_relative_date_only_removes_the_superseded_question(self):
        spec = read(P024 / 'human_22/program/request.json')
        ready = normalize_saved_spec(spec)
        self.assertTrue(ready['ready'])
        self.assertFalse(spec['ready'])
        request, unsupported = constructor_input(ready)
        self.assertFalse(unsupported)
        self.assertEqual(request['outbound_arrival_stations'], ['北京大兴国际机场'])
        self.assertEqual(request['outbound_arrival_period'], ('06:00', '12:00'))
        other_issue = {'field': 'people', 'code': 'missing_required_field', 'message': '请填写人数。'}
        spec['issues'].append(other_issue)
        still_pending = normalize_saved_spec(spec)
        self.assertFalse(still_pending['ready'])
        self.assertEqual(still_pending['issues'], [other_issue])

    def test_nightly_budget_validates_all_rooms_and_every_night(self):
        req = {'id': 'cost', 'field': 'lodging_budget', 'strength': 'hard',
               'scope': 'each_night', 'operator': 'lte', 'unit': 'CNY/person/night', 'value': 300, 'source': {}}
        plan = {'itinerary': [{'day': 1, 'activities': [
            {'type': 'accommodation', 'rooms': 2, 'price': 310}]},
                              {'day': 2, 'activities': []}]}
        report = {'issues': [], 'snapshot_evidence': {'overnight_stays': [{'day': 1}]}, 'total_cost': 1000}
        checked, = _hard_checks([req], [], plan, report, 2)
        self.assertEqual(checked['status'], 'violated')
        plan['itinerary'][0]['activities'][0]['price'] = 300
        checked, = _hard_checks([req], [], plan, report, 2)
        self.assertEqual(checked['status'], 'passed')

    def test_named_hotel_uses_resolved_entity_for_every_night(self):
        req = {'id': 'hotel-1', 'field': 'hotel', 'strength': 'hard',
               'scope': 'lodging', 'operator': 'select', 'value': '指定酒店', 'unit': None, 'source': {}}
        plan = {'itinerary': [{'day': d, 'activities': []} for d in (1, 2, 3)]}
        stays = [{'day': d, 'name': '指定酒店', 'id': 'accommodation:1'} for d in (1, 2)]
        report = {'issues': [], 'snapshot_evidence': {'overnight_stays': stays}}
        pin = {'hotel-1': {'id': 'accommodation:1', 'name': '指定酒店'}}
        checked, = _hard_checks([req], [], plan, report, 1, pin)
        self.assertEqual(checked['status'], 'passed')
        stays[1]['id'] = 'accommodation:2'
        checked, = _hard_checks([req], [], plan, report, 1, pin)
        self.assertEqual(checked['status'], 'violated')
        self.assertEqual(_hard_checks([req], [], plan, report, 1)[0]['status'], 'insufficient_evidence')

    def test_route_ranks_distance_and_still_requires_validation(self):
        outbound = {'type': 'train', 'end': '到达站'}
        near = {'type': 'attraction', 'position': '近处', 'start_time': '10:00'}
        far = {'type': 'attraction', 'position': '远处', 'start_time': '09:00'}
        inbound = {'type': 'train', 'start': '返程站'}
        original = {'day': 1, 'activities': [outbound, far, near, inbound]}
        rebuilt = {'day': 1, 'activities': [outbound, near, far, inbound]}
        plan = {'itinerary': [original]}
        coords = {'到达站': 0, '近处': 1, '远处': 5, '返程站': 6}
        traveler = SimpleNamespace(points=coords, distance=lambda a, b: abs(coords[a] - coords[b]),
                                   route_calls=0, check=lambda: None)
        for passed in (True, False):
            baseline = {'passed': True}, {'city_transit_minutes': 10, 'total_cost_cny': 50}, []
            issues = [] if passed else [{'code': 'departure_buffer'}]
            alternative = {'passed': passed, 'issues': issues}, {'city_transit_minutes': 20, 'total_cost_cny': 60}, []
            with (
                self.subTest(passed=passed),
                patch.object(sketch_planning, 'check_snapshot_reference'),
                patch.object(sketch_planning, 'prepare_snapshot_routes'),
                patch.object(sketch_planning, 'constructor_input', return_value=({'appointments': []}, [])),
                patch.object(sketch_planning, 'Constructor', return_value=traveler),
                patch.object(sketch_planning, '_rebuild_day', return_value=rebuilt),
                patch.object(sketch_planning, 'audit', side_effect=[baseline, alternative, alternative]),
            ):
                result = optimize_day({}, {'days': 1}, plan, 1, None, None)
                self.assertEqual(result['distance']['before_km'], 14)
                self.assertEqual(result['distance']['after_km'], 6 if passed else 14)
                self.assertEqual(result['itinerary']['itinerary'], [rebuilt if passed else original])


if __name__ == '__main__':
    unittest.main()
