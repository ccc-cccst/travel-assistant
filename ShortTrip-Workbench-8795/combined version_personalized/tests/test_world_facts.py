import json
from pathlib import Path
import unittest

from chinatravel.personalization.cli import environment, read, ROOT
from chinatravel.personalization.tripspec import interpret
from chinatravel.personalization.world_facts import WorldFacts, opening


V20 = ROOT.parent / 'ShortTrip/artifacts/data/6de12f4'
P009 = ROOT / 'result/experiments/P009_stable_planning'


class OpeningTests(unittest.TestCase):
    def test_overnight_is_preserved_and_equal_clock_is_not_full_day(self):
        access = {'time_complete': True}
        overnight = opening({'opentime': '18:00', 'endtime': '02:00'}, access, 'restaurant')
        self.assertEqual(overnight['windows_minutes'], [[1080, 1560]])
        self.assertEqual(opening({'opentime': '00:00', 'endtime': '00:00'}, access, 'attraction')['status'], 'unknown')


@unittest.skipUnless(V20.is_dir(), 'requires the installed pinned V20 snapshot')
class WorldFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment(V20)
        cls.facts = WorldFacts.configured(V20)

    def test_pinned_place_and_source_are_independent_of_candidate_filter(self):
        result = self.facts.snapshot('上海', pins=['上海迪士尼度假区', 'Shanghai Disney Resort'], terms=['博物馆'], limit=1)
        first, alias = (p['records'][0] for p in result['pinned_places'])
        self.assertEqual(first['id'], alias['id'])
        self.assertTrue((self.facts.installation / first['source']['file']).is_file())
        self.assertEqual(len(first['source']['sha256']), 64)
        self.assertEqual(first['price']['status'], 'snapshot_reference')
        self.assertFalse(first['price']['verified_for_date'])
        self.assertEqual(result['candidates']['attraction'][0]['kind'], 'attraction')
        json.dumps(result, allow_nan=False)

    def test_missing_price_and_historical_hotel_price_stay_separate(self):
        facts = self.facts
        restaurant = facts.place('厦门', '山丽妍沙茶面(大同路店)', 'restaurant')['records'][0]
        self.assertIsNone(restaurant['price']['value'])
        self.assertEqual(restaurant['price']['status'], 'unknown')
        hotel = facts.place('厦门', '厦门国际会议中心酒店', 'accommodation')['records'][0]
        self.assertEqual(hotel['price']['status'], 'historical_reference')
        self.assertIsNone(hotel['price']['unit'])
        self.assertFalse(hotel['price']['usable_for_snapshot_budget'])

    def test_segmented_hours_keep_the_gap(self):
        record = self.facts.place('厦门', '香遇巴厘(新中林店)', 'restaurant')['records'][0]
        self.assertEqual(record['opening']['windows_minutes'], [[660, 840], [1020, 1290]])
        self.assertFalse(record['opening']['verified_for_date'])

    def test_date_scoped_flights_do_not_turn_into_current_inventory(self):
        facts = self.facts
        dated = facts.intercity('北京', '厦门', 'airplane', travel_date='2026-11-20', limit=2)
        self.assertEqual(dated['counts']['dated_observations'], 19)
        self.assertEqual(dated['counts']['snapshot_records'], 0)
        self.assertEqual(len(dated['dated_observations']), 2)
        self.assertTrue(all(r['availability'] == 'unknown' and not r['strict_budget_eligible']
                            for r in dated['dated_observations']))
        other_date = facts.intercity('北京', '厦门', 'airplane', travel_date='2099-01-01')
        self.assertEqual(other_date['dated_observations'], [])
        self.assertEqual(other_date['status'], 'no_records_in_snapshot')

    def test_route_price_preserves_vehicle_count_and_estimate_scope(self):
        route = self.facts.route('上海', '外滩', '豫园', 'taxi', '10:00', people=5)
        self.assertEqual(route['status'], 'estimated')
        self.assertFalse(route['verified_for_date'])
        self.assertEqual(route['legs'][0]['cars'], 2)
        self.assertEqual(route['legs'][0]['cost'], 2 * route['legs'][0]['price'])

    def test_intercity_reports_minutes_from_snapshot_hours(self):
        trains = self.facts.intercity('南京', '上海', 'train', limit=10)['records']
        train = next(r for r in trains if r['service'] == 'K5837')
        self.assertEqual(train['source_duration_hours'], 5.9)
        self.assertEqual(train['duration_minutes'], 354)

    def test_lookup_of_named_rail_service_preserves_class_and_direction(self):
        selected = self.facts.intercity('苏州', '上海', 'train', service='G7029', limit=1)
        train, = selected['records']
        self.assertEqual((train['service'], train['train_type']), ('G7029', '高铁'))
        self.assertEqual(train['source']['record_key']['TrainID'], train['service'])
        self.assertEqual(selected['counts']['snapshot_records'], 1)
        reversed_route = self.facts.intercity('上海', '苏州', 'train', service='G7029')
        self.assertEqual(reversed_route['records'], [])

    def test_trip_reads_named_targets_and_filters_high_speed_before_limit(self):
        spec = read(P009 / 'constraints_run_04/human_69/request.json')
        snapshot = self.facts.trip_snapshot(spec, limit=1)
        grounding, = snapshot['place_groundings']
        self.assertEqual(grounding['status'], 'ambiguous')
        self.assertEqual(grounding['query'], '迪士尼')
        names = {r['name']: r['id'] for r in grounding['records']}
        self.assertNotEqual(names['上海迪士尼度假区'], names['迪士尼小镇'])
        self.assertNotIn('上海迪士尼乐园', names)
        for direction, origin, target in [('outbound', '苏州', '上海'), ('return', '上海', '苏州')]:
            queries, = snapshot['intercity'][direction]
            record, = queries['records']
            self.assertEqual((queries['origin'], queries['destination']), (origin, target))
            self.assertEqual(record['train_type'], '高铁')
            self.assertEqual(record['service'], record['source']['record_key']['TrainID'])
            self.assertEqual(record['availability'], 'unknown')
        hotel, = snapshot['candidates']['accommodation']
        self.assertTrue(hotel['price']['usable_for_snapshot_budget'])
        self.assertFalse(snapshot['verification_scope']['actual_room_type_and_occupancy_verified'])
        self.assertEqual(snapshot['snapshot_id'], self.facts.trip_snapshot(spec, limit=1)['snapshot_id'])

    def test_trip_filters_amenities_and_meal_budget_independently_of_room_type(self):
        spec = read(P009 / 'constraints_run_04/medium_140/request.json')
        snapshot = self.facts.query(spec)
        hotels = snapshot['candidates']['accommodation']
        meals = snapshot['candidates']['restaurant']
        self.assertTrue(hotels and meals)
        for hotel in hotels:
            self.assertIn('管家服务', hotel['tags']['values'])
            self.assertEqual(hotel['tags']['source_field'], 'featurehoteltype')
            self.assertTrue(hotel['source']['sha256'])
        next(i for i in spec['items'] if i['field'] == 'room_type')['value'] = '双床房'
        changed = self.facts.query(spec)
        self.assertEqual(changed['candidates']['accommodation'], hotels)
        self.assertEqual(changed['coverage']['accommodation'], snapshot['coverage']['accommodation'])
        for meal in meals:
            self.assertLessEqual(meal['price']['value'], 180)
            self.assertEqual(meal['price']['unit'], 'per_person_per_meal')
        self.assertTrue(any('火锅' in tag for r in meals for tag in r['tags']['values']))
        for group, in snapshot['intercity'].values():
            self.assertTrue(group['records'])
            self.assertTrue(all(r['mode'] == 'airplane' for r in group['records']))

    def test_trip_keeps_appointment_and_requested_dates_separate_from_reference_hours(self):
        form = {**read(ROOT / 'examples/four_day_appointment.json'), 'start_date': '2026-12-01'}
        spec = interpret(form, self.facts.sandbox.cities)
        snapshot = self.facts.query({'action': 'trip_snapshot', 'spec': spec, 'limit': 1})
        grounding, = snapshot['place_groundings']
        place, = grounding['records']
        self.assertEqual(place['name'], '上海博物馆')
        self.assertEqual(snapshot['candidates']['attraction'][0]['id'], place['id'])
        self.assertEqual(place['opening']['windows_minutes'], [[540, 1020]])
        self.assertEqual(grounding['visit'], {'day': 3, 'date': '2026-12-03', 'window_minutes': [900, 960],
                                             'window_status': 'within_reference', 'verified_for_date': False})
        self.assertEqual(snapshot['intercity']['outbound'][0]['requested_date'], '2026-12-01')
        self.assertEqual(snapshot['intercity']['return'][0]['requested_date'], '2026-12-04')
        with self.assertRaisesRegex(ValueError, '待确认'):
            self.facts.trip_snapshot({**spec, 'ready': False})

    def test_trip_direction_rules_leave_other_direction_unrestricted(self):
        spec = read(P009 / 'constraints_run_04/human_69/request.json')
        mode = next(a for a in spec['items'] if a['field'] == 'intercity_mode')
        mode.update(scope='outbound', value='高铁G')
        snapshot = self.facts.trip_snapshot(spec, limit=2)
        outbound, = snapshot['intercity']['outbound']
        backward, = snapshot['intercity']['return']
        self.assertTrue(outbound['records'])
        self.assertTrue(all(r['service'].startswith('G') for r in outbound['records']))
        self.assertIn('snapshot_records_before_restrictions', outbound['counts'])
        self.assertNotIn('snapshot_records_before_restrictions', backward['counts'])


if __name__ == '__main__':
    unittest.main()
