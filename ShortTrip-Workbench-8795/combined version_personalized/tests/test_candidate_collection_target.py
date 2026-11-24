"""Exercise the real shared collector through all three planner adapters."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from chinatravel.agent.nesy_agent.llm_driven_rec import LLMDrivenAgent
from chinatravel.soft_constraints.agent import SoftLLMDrivenAgent, CandidateDeadline
from experiments.timing_probe.day_sets import daily_agent, DaySearchComplete
from experiments.timing_probe.combo_schedule import combination_agent
from experiments.timing_probe.run_planner import command


class CandidateTargetTests(unittest.TestCase):
    def collect(self, mode, count):
        classes = {'baseline': SoftLLMDrivenAgent,
                   'day_sets': daily_agent(SoftLLMDrivenAgent, lambda _a: None),
                   'combo_schedule': combination_agent(SoftLLMDrivenAgent, lambda _a: None)}
        def setup(agent, **_kwargs):
            agent.backbone_llm = SimpleNamespace(_send_request=lambda *_a: None,
                input_token_count=0, output_token_count=0, input_token_maxx=0)
        sizes=[]
        with patch.object(LLMDrivenAgent, '__init__', setup):
            agent=classes[mode](candidate_limit=10, on_candidate=lambda pool:sizes.append(len(pool.items)))
        agent._score=lambda p:{'total_penalty':p['itinerary'][0]['activities'][0]['cost']}
        agent._set_done=lambda _p:True
        agent._appointments=lambda:[]
        agent._set_candidates=0
        def validate(_agent, _query, itinerary, _root):
            return True,dict(start_city='A',target_city='B',people_number=1,itinerary=itinerary)
        def search(*_args, **_kwargs):
            for index in range(count):
                itinerary=[{'day':1,'activities':[{'type':'attraction','position':str(index),'cost':10-index}]}]
                try:
                    agent.constraints_validation({},itinerary,{})
                    if index==0:
                        agent.constraints_validation({},itinerary,{}) # duplicate must not fill the pool
                except DaySearchComplete:
                    agent._set_candidates=0 # local slice yields; global collection must continue
            raise CandidateDeadline()
        with patch.object(LLMDrivenAgent,'run',search),patch.object(LLMDrivenAgent,'constraints_validation',validate):
            success,result=agent.run({})
        self.assertTrue(success)
        self.assertEqual(sizes,list(range(1,count+1)))
        self.assertEqual(len(agent.pool.items),count)
        self.assertEqual(agent.pool.duplicate_count,1)
        self.assertEqual(agent.selection['candidate_target'],10)
        self.assertEqual(agent.selection['selected_ordinal'],count)
        self.assertEqual(result['itinerary'][0]['activities'][0]['position'],str(count-1))
        return agent.selection

    def test_all_planners_continue_after_first_until_ten_then_select_best(self):
        for mode in ('baseline','day_sets','combo_schedule'):
            with self.subTest(mode=mode):
                selection=self.collect(mode,10)
                self.assertTrue(selection['complete'])
                self.assertEqual(selection['stop_reason'],'candidate_limit_reached')

    def test_deadline_keeps_partial_pool_and_selects_best(self):
        for mode in ('baseline','day_sets','combo_schedule'):
            with self.subTest(mode=mode):
                selection=self.collect(mode,4)
                self.assertFalse(selection['complete'])
                self.assertEqual(selection['stop_reason'],'wall_limit_reached')

    def test_cli_preserves_requested_candidate_count_for_both_models(self):
        for provider in ('deepseek','qwen'):
            args=SimpleNamespace(query='query.json',out='out',planner='combo_schedule',
                time_policy='flexible',seconds=1800,provider=provider,endpoint='unused',candidate_limit=10)
            cmd=command(args)
            self.assertEqual(cmd[cmd.index('--candidate-limit')+1],'10')


if __name__=='__main__':
    unittest.main()
