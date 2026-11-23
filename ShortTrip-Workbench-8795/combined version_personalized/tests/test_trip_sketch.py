from copy import deepcopy
import json
import os
import unittest
from unittest.mock import Mock, patch

from chinatravel.personalization.cli import ROOT, read
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.tripspec import ModelReply


P010 = ROOT / 'result/experiments/P010_trip_world_snapshot/run_02'


def snapshot(case):
    return read(P010 / case / 'WORLD_FACTS.json')


def alternate_appointment_layout(world):
    local = propose_trip_sketches(world)['sketches']
    layout = deepcopy(local[0]['days'])
    used = {ref for sketch in local for day in sketch['days'] for ref in day['core_ids']}
    extra = next(p['id'] for p in world['candidates']['attraction'] if p['id'] not in used)
    layout[0]['core_ids'] = [extra]
    layout[0]['alternative_ids'] = []
    return layout


class TripSketchTests(unittest.TestCase):
    def test_program_uses_saved_appointment_in_every_layout(self):
        world = snapshot('four_day_appointment')
        result = propose_trip_sketches(world)
        self.assertEqual(result, propose_trip_sketches(world))
        self.assertEqual(result['snapshot_id'], world['snapshot_id'])
        self.assertEqual(result['model']['attempts'], 0)
        self.assertEqual(len(result['sketches']), 2)
        lock, = result['fixed_appointments']
        self.assertEqual((lock['id'], lock['day'], lock['window_minutes']),
                         ('attraction:shanghai:9', 3, [900, 960]))
        for draft in result['sketches']:
            self.assertEqual([d['day'] for d in draft['days'] if lock['id'] in d['core_ids']], [3])

    def test_ambiguous_disney_goal_remains_unconfirmed_when_proposed(self):
        world = snapshot('human_69')
        result = propose_trip_sketches(world)
        resort_days = []
        for draft in result['sketches']:
            goal, = draft['goal_evidence']
            place, = goal['places']
            self.assertEqual(goal['status'], 'ambiguous_reference')
            self.assertEqual(place['id'], 'attraction:shanghai:0')
            resort_days.append(place['days'][0])
            self.assertEqual(draft['days'][place['days'][0] - 1]['core_ids'], [place['id']])
        self.assertEqual(resort_days, [1, 2])

    def test_matching_theme_and_cuisine_can_share_a_day_with_nearby_lodging(self):
        world = read(ROOT / 'result/experiments/P027_room_and_relative_date/regression_01'
                     / 'human_37/WORLD_FACTS.json')
        result = propose_trip_sketches(world)
        sketch, = [s for s in result['sketches'] if s['source'] == 'nearby_experiences']
        theme = next(r for r in world['requirements'] if r['field'] == 'experience_theme')
        meal = next(r for r in world['requirements'] if r['field'] == 'cuisine')
        evidence = {item['requirement_id']: item for item in sketch['goal_evidence']}
        days = lambda req: {day for p in evidence[req['id']]['places'] for day in p['days']}
        self.assertEqual(days(theme) & days(meal), {2})
        self.assertEqual(sketch['days'][0]['lodging_ids'], sketch['days'][1]['lodging_ids'])
        self.assertFalse(sketch['days'][-1]['lodging_ids'])

    def test_model_can_add_a_distinct_layout_in_one_request(self):
        world = snapshot('four_day_appointment')
        layouts = [alternate_appointment_layout(world)]
        provider = Mock(return_value=ModelReply(draft={'layouts': layouts}, attempts=1,
                                                usage={'total_tokens': 99}, model='stub'))
        result = propose_trip_sketches(world, use_model=True, provider=provider)
        provider.assert_called_once()
        payload = provider.call_args.args[1]
        self.assertEqual(payload['appointments'], [[3, 'attraction:shanghai:9']])
        self.assertTrue(all('source' not in item for item in payload['requirements']))
        self.assertEqual(result['model']['usage'], {'total_tokens': 99})
        model, = [draft for draft in result['sketches'] if draft['source'] == 'model']
        self.assertEqual(model['days'], layouts[0])

    def test_wrong_place_type_and_moved_appointment_are_rejected(self):
        world = snapshot('four_day_appointment')
        incorrect = alternate_appointment_layout(world)
        moved = deepcopy(incorrect)
        incorrect[0]['core_ids'] = [world['candidates']['restaurant'][0]['id']]
        moved[1]['core_ids'] = moved[2]['core_ids']
        moved[2]['core_ids'] = []
        reply = ModelReply(draft={'layouts': [incorrect, moved]}, attempts=1)
        provider = Mock(return_value=reply)
        result = propose_trip_sketches(world, use_model=True, provider=provider)
        self.assertEqual([s['source'] for s in result['sketches']], ['program', 'program'])
        self.assertEqual([i['reason'] for i in result['model']['rejected']],
                         ['unknown_or_wrong_place_type', 'appointment_missing_or_moved'])

    def test_unknown_id_is_rejected_and_model_timeout_keeps_program_layouts(self):
        world = snapshot('four_day_appointment')
        missing = alternate_appointment_layout(world)
        missing[0]['core_ids'] = ['attraction:does-not-exist:999']
        unknown = Mock(return_value=ModelReply(draft={'layouts': [missing]}, attempts=1))
        result = propose_trip_sketches(world, use_model=True, provider=unknown)
        self.assertEqual(result['model']['rejected'][0]['reason'], 'unknown_or_wrong_place_type')
        timeout = Mock(return_value=ModelReply(error='APITimeoutError', attempts=1))
        result = propose_trip_sketches(world, use_model=True, provider=timeout)
        timeout.assert_called_once()
        self.assertEqual(result['model']['error'], 'APITimeoutError')
        self.assertEqual(result['sketches'], propose_trip_sketches(world)['sketches'])

    def test_real_provider_without_credentials_reports_fallback(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
            result = propose_trip_sketches(snapshot('medium_140'), use_model=True)
        self.assertEqual(result['model']['error'], 'missing_model_credentials')
        self.assertEqual(result['model']['attempts'], 0)
        self.assertEqual(len(result['sketches']), 2)

    def test_deepseek_adapter_sends_one_structured_sdk_request(self):
        world = snapshot('four_day_appointment')
        draft = {'layouts': [alternate_appointment_layout(world)]}
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-only'}), patch('openai.OpenAI') as build:
            client = build.return_value.__enter__.return_value
            create = client.chat.completions.create
            response = create.return_value
            response.model = 'deepseek-flash'
            response.usage.model_dump.return_value = {'prompt_tokens': 40, 'completion_tokens': 5}
            choice = Mock(finish_reason='stop')
            choice.message.content = json.dumps(draft)
            response.choices = [choice]
            result = propose_trip_sketches(world, use_model=True)
        build.assert_called_once()
        self.assertEqual(build.call_args.kwargs['max_retries'], 0)
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs['response_format'], {'type': 'json_object'})
        self.assertEqual(create.call_args.kwargs['max_tokens'], 1536)
        self.assertEqual(result['model']['usage'], {'prompt_tokens': 40, 'completion_tokens': 5})
        self.assertEqual(len([s for s in result['sketches'] if s['source'] == 'model']), 1)

    def test_unresolved_fixed_appointment_blocks_model(self):
        world = snapshot('four_day_appointment')
        world['place_groundings'][0].update(status='no_catalog_match', records=[])
        provider = Mock()
        result = propose_trip_sketches(world, use_model=True, provider=provider)
        provider.assert_not_called()
        self.assertEqual(result['state'], 'needs_confirmation')
        self.assertEqual(result['issues'][0]['code'], 'appointment_location_unresolved')


if __name__ == '__main__':
    unittest.main()
