import unittest

from chinatravel.personalization.place_preferences import update_place_preference
from chinatravel.personalization.plan_assessment import _hard_checks


PLACE_ID = 'attraction:hangzhou:sample'


class Facts:
    def place(self, city, key, kind):
        if city == '杭州' and key in (PLACE_ID, '样例景点', '旧称') and kind == 'attraction':
            return {'status': 'found', 'records': [{
                'id': PLACE_ID, 'name': '样例景点', 'routable_in_snapshot': True, 'planning_catalog': True,
            }]}
        return {'status': 'no_catalog_match', 'records': []}


class PlacePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.spec = {'request_revision': 1, 'items': [
            {'field': 'destination_city', 'value': '杭州', 'source': {'type': 'form'}},
        ]}
        self.facts = Facts()

    def test_preference_changes_replace_only_the_map_item_and_can_be_undone(self):
        want = update_place_preference(self.spec, self.facts, PLACE_ID, 'want')
        self.assertEqual(want['items'][-1]['operator'], 'prefer')
        self.assertEqual(want['items'][-1]['strength'], 'soft')
        self.assertIs(update_place_preference(want, self.facts, PLACE_ID, 'want'), want)
        must = update_place_preference(want, self.facts, PLACE_ID, 'must')
        self.assertEqual(len(must['items']), 2)
        self.assertEqual(must['items'][-1]['strength'], 'hard')
        clear = update_place_preference(must, self.facts, PLACE_ID, 'clear')
        self.assertEqual(clear['request_revision'], 4)
        self.assertEqual(clear['items'], self.spec['items'])

    def test_a_reserved_place_cannot_be_skipped_using_an_alias(self):
        self.spec['items'].append({'field': 'appointment', 'value': {'name': '旧称'}, 'source': {'type': 'form'}})
        with self.assertRaisesRegex(ValueError, '预约'):
            update_place_preference(self.spec, self.facts, PLACE_ID, 'skip')

    def test_exclusion_checks_the_scheduled_ids(self):
        req = {'id': 'r1', **update_place_preference(self.spec, self.facts, PLACE_ID, 'skip')['items'][-1]}
        pins = {'r1': PLACE_ID}
        plan = {'itinerary': [{'day': 1, 'activities': []}]}
        for places, status in (([PLACE_ID], 'violated'), (['other'], 'passed')):
            report = {'snapshot_evidence': {'attraction_ids': places}, 'issues': []}
            self.assertEqual(_hard_checks([req], [], plan, report, 1, excluded=pins)[0]['status'], status)
