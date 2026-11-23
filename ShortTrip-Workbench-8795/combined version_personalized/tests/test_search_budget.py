from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from chinatravel.personalization import trip_recovery
from chinatravel.personalization.cli import save
from chinatravel.personalization.sketch_planning import additional_layouts


class SearchBudgetTests(TestCase):
    def test_extra_directions_keep_named_places_and_appointments_on_their_days(self):
        day = {'day':1, 'core_ids':['booked','named','ordinary'], 'alternative_ids':['replacement']}
        sketch = {'id':'first','days':[day]}
        layouts = {'fixed_appointments':[{'id':'booked'}], 'sketches':[sketch]}
        snapshot = {'place_groundings':[{'records':[{'id':'named'}]}]}
        expanded = additional_layouts(layouts,snapshot)['sketches']
        self.assertEqual(expanded[0]['days'][0]['core_ids'], ['booked','named','replacement'])
        self.assertEqual(day['core_ids'], ['booked','named','ordinary'])

    def run_recovery(self, end_of_initial_run):
        deadline = 60
        now = [0]
        calls = []
        spec = {'items':[]}
        proposals = [(spec, {'field':'hotel','after':hotel}, 'preview',False) for hotel in ('A','B')]

        def plan(spec, folder, env, facts, **opts):
            self.assertEqual(opts['search_deadline'], deadline)
            calls.append(opts)
            if len(calls)==1:
                now[0] = end_of_initial_run
                return {'state':'search_timeout','message':'timeout'}
            self.assertLess(opts['finish_by'],deadline)
            save(folder/'PLAN_VIEW.json',{'candidates':[]})
            save(folder/'WORLD_FACTS.json',{'snapshot_id':'sample'})
            return {'state':'complete','message':'OK','candidate_count':opts['candidates']}

        with (TemporaryDirectory() as temp, patch.object(trip_recovery,'normalize_saved_spec',return_value=spec),
              patch.object(trip_recovery,'perf_counter',side_effect=lambda:now[0]),
              patch.object(trip_recovery,'_proposals',return_value=proposals),
              patch.object(trip_recovery,'_city_draft',return_value=None),
              patch.object(trip_recovery,'run_trip_plan',side_effect=plan)):
            trip_recovery.run_trip_with_recovery(spec,Path(temp),None,None,candidates=15,search_deadline=deadline)
        return calls

    def test_two_previews_share_the_remaining_slots_and_time(self):
        calls = self.run_recovery(20)
        self.assertEqual([c['candidates'] for c in calls[1:]], [8,7])

    def test_expired_initial_budget_cannot_start_another_search(self):
        self.assertEqual(len(self.run_recovery(60)), 1)
