import json
import unittest
from types import SimpleNamespace

import pandas as pd

from chinatravel.personalization.catalog import PreferenceCatalog
from chinatravel.personalization.policy import PreferencePolicy
from chinatravel.personalization.request import normalize_request
from chinatravel.personalization.trip_recall import context, propose, read_directions


class TripRecallTests(unittest.TestCase):
    def setUp(self):
        self.rows=pd.DataFrame([{'name':f'地点{i}','type':'公园','price':0,
                                'opentime':'08:00','endtime':'20:00'} for i in range(25)])
        self.request=normalize_request({'start_city':'南京','target_city':'上海','days':4,
                                       'people_number':2,'nature_language':''})
        self.catalog=PreferenceCatalog.from_records(city='上海',attractions=self.rows.to_dict('records'))
        self.catalog.env=SimpleNamespace(poi=SimpleNamespace(search=lambda city,name:(31.,121.)))
        self.policy=PreferencePolicy(self.request,self.catalog)

    def test_invented_and_boolean_ids_are_rejected(self):
        answer=json.dumps({'directions':[{'attractions':[999]}, {'attractions':[True]},
                      {'attractions':[22,23,22]}, {'attractions':[23,22]}]})
        self.assertEqual([g['attractions'] for g in read_directions(answer,set(range(25)))],[[22,23]])
        self.assertEqual(read_directions('not json',set(range(25))),[])

    def test_uses_whole_city_and_confirmed_request_without_oracle(self):
        value=context(self.request,self.rows,self.catalog)
        self.assertEqual(len(value['attractions']),25)
        self.assertIsNone(value['facts']['budget'])
        self.assertEqual(value['scope'].split(';')[0],'complete_active_city_catalog')
        self.assertNotIn('hard_logic_py',str(value))

    def test_new_direction_reaches_daily_pool_with_pinned_and_local_choices(self):
        self.policy.trip_directions=[{'label':'另一片区','attractions':[22,23,24]}]
        ids=self.policy.recall_day(self.rows,list(range(25)),{1},set(),set(),'起点','终点')
        self.assertEqual(len(ids),12)
        self.assertIn(1,ids)
        self.assertTrue({22,23,24}<=set(ids))
        self.assertIn(0,ids)

    def test_visited_and_other_date_appointments_do_not_consume_new_slots(self):
        self.policy.trip_directions=[{'attractions':[22,23,24]}]
        ids=self.policy.recall_day(self.rows,list(range(25)),{1},{'地点22'},{'地点23'},'起点','终点')
        self.assertNotIn(22,ids);self.assertNotIn(23,ids);self.assertIn(24,ids)
        self.assertEqual(len(ids),12)

    def test_missing_valid_model_directions_preserves_baseline(self):
        ordered=list(range(25))
        self.assertEqual(self.policy.recall_day(self.rows,ordered,set(),{'地点0'},set(),'起点','终点'),
                         self.policy.recall(self.rows,ordered))

    def test_dedupe_ablation_keeps_geographic_order_without_global_candidates(self):
        self.policy.dedupe_before_recall=True
        ids=self.policy.recall_day(self.rows,list(range(25)),{1},{'地点0'},{'地点2'},'起点','终点')
        self.assertEqual(ids,[1,*range(3,14)])

    def test_structured_short_output_one_call(self):
        calls=[]
        def model(messages,**kwargs):
            calls.append((messages,kwargs));return '{"directions":[{"attractions":[22,23]}]}'
        reply=propose(model,context(self.request,self.rows,self.catalog))
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1]['max_tokens'],900)
        self.assertEqual(read_directions(reply,set(range(25)))[0]['attractions'],[22,23])

    def run_prepare(self,model):
        from chinatravel.personalization.agent import personalized_agent
        cls=personalized_agent(object,lambda agent:None,self.request,candidate_recall='trip_api')
        # Cold start: legacy counters intentionally do not exist before search.
        agent=SimpleNamespace(memory={'attractions':self.rows},personalization_policy=self.policy,
            backbone_llm=model,trip_recall_report={'mode':'trip_api','state':'not_requested'},
            _trip_recall_active=False)
        cls.prepare_trip_recall(agent)
        self.assertFalse(agent._trip_recall_active)
        self.assertGreaterEqual(agent.trip_recall_report['seconds'],0)
        return agent

    def test_cold_start_does_not_depend_on_search_counters(self):
        agent=self.run_prepare(lambda *args,**kwargs:'{"directions":[{"attractions":[22,23]}]}')
        self.assertEqual(agent.trip_recall_report['state'],'active')
        self.assertEqual(self.policy.trip_directions[0]['attractions'],[22,23])

    def test_front_loaded_model_call_has_its_own_budget(self):
        from chinatravel.personalization.agent import personalized_agent
        cls=personalized_agent(object,lambda agent:None,self.request,candidate_recall='trip_api')
        agent=cls.__new__(cls)
        agent._trial_deadline=None;agent._trip_recall_active=True
        bounded=agent.limit_model_request({'timeout':180,'max_tokens':4096})
        self.assertEqual(bounded['timeout'],30)
        self.assertEqual(bounded['max_tokens'],900)

    def test_timeout_and_invalid_output_are_explicit_fallbacks(self):
        from openai import APITimeoutError
        def timeout(*args,**kwargs):raise APITimeoutError(request=SimpleNamespace(url='https://example.test'))
        agent=self.run_prepare(timeout)
        self.assertEqual(agent.trip_recall_report['state'],'fallback')
        self.assertEqual(agent.trip_recall_report['reason'],'APITimeoutError')
        agent=self.run_prepare(lambda *args,**kwargs:'{"directions":[{"attractions":[999]}]}')
        self.assertEqual(agent.trip_recall_report['reason'],'no_valid_direction_ids')


if __name__=='__main__':unittest.main()
