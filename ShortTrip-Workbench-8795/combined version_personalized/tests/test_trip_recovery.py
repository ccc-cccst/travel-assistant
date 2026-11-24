from copy import deepcopy
import unittest

from chinatravel.personalization.cli import ROOT, read
from chinatravel.personalization.trip_recovery import _city_draft, _proposals


P031 = ROOT/'result/experiments/P031_first_plan/after_02'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        path = P031/'human1000_666'
        self.spec = read(path/'request.json')
        self.snapshot = read(path/'WORLD_FACTS.json')
        self.status = read(path/'STATUS.json')

    def test_timeout_can_reuse_a_verified_place_without_a_new_lookup(self):
        path = P031/'easy_24'
        spec = read(path/'request.json')
        draft = _city_draft(spec, read(path/'WORLD_FACTS.json'), facts=None, deadline=0)
        self.assertEqual(draft['city'], '苏州')
        self.assertEqual(len(draft['stops']), 1)
        self.assertIsNone(draft['segment'])

    def test_changing_outbound_preserves_return_restriction_and_original(self):
        rule = next(i for i in self.spec['items'] if i['field'] == 'intercity_mode')
        rule['scope'] = 'round_trip'
        original = deepcopy(self.spec)
        (revised, change, _, _), = _proposals(self.spec, self.status, self.snapshot)
        rules = {i['scope']: i['value'] for i in revised['items'] if i['field'] == 'intercity_mode'}
        self.assertEqual(rules, {'outbound': '火车', 'return': '飞机'})
        self.assertEqual(change['scope'], 'outbound')
        self.assertEqual(self.spec, original)

    def test_booked_transport_and_incomplete_snapshot_do_not_offer_mode_switch(self):
        self.snapshot['intercity']['outbound'][0]['errors'].append('Lookup failed')
        self.assertEqual(list(_proposals(self.spec, self.status, self.snapshot)), [])
        self.snapshot['intercity']['outbound'][0]['errors'].clear()
        self.spec['raw_text'] += '航班已订，不能换。'
        self.assertEqual(list(_proposals(self.spec, self.status, self.snapshot)), [])

    def test_conflict_preview_does_not_record_consent(self):
        path = P031/'human_9'
        spec = read(path/'request.json')
        variants = list(_proposals(spec, read(path/'STATUS.json'), None))
        self.assertCountEqual([change['after'] for _, change, _, _ in variants], [5, 7])
        for revised, _, _, _ in variants:
            day = next(i for i in revised['items'] if i['field'] == 'days')
            self.assertEqual(day['additional_sources'][-1]['type'], 'recovery_assumption')
        self.assertFalse(spec['ready'])

    def test_budget_is_opt_in_and_a_combined_adjustment_is_one_revision(self):
        path = P031/'easy_24'
        spec = read(path/'request.json')
        status = read(path/'STATUS.json')
        snapshot = read(path/'WORLD_FACTS.json')
        self.assertEqual(len(list(_proposals(spec, status, snapshot))), 1)
        _, (draft, change, _, _)=list(_proposals(spec, status, snapshot, True))
        self.assertEqual([part['after'] for part in change['parts']], [2, 2000])
        self.assertEqual(draft['request_revision'], spec['request_revision']+1)
        spec['raw_text']+='预算绝不超过1600元。'
        self.assertEqual(len(list(_proposals(spec, status, snapshot, True))), 1)
