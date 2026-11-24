"""Exercise real Windows-spawn processes without models or trip searches."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from experiments.parallel_search.pool import run_pool
from experiments.parallel_search.runtime import RecordedModel, ReplayMiss
from experiments.parallel_search.evaluation import day_sets,pareto


def fixture_worker(index, ids, bundle, stop, messages, deadline, out, options):
    def emit(event, **kw):
        messages.put({'event':event,'worker':index,'at':time.monotonic(),**kw})
    bundle.setdefault('private',[]).append(index)
    emit('ready')
    status='exhausted'
    try:
        for i in ids:
            emit('task_start',task=i)
            task=bundle['tasks'][i]
            if task.get('error'):
                status='error'
                return
            if stop.wait(task['delay']):
                status='stopped'
                return
            emit('task_end',task=i)
            if task.get('candidate'):
                emit('candidate',plan=task.get('plan',{'task':i,'private':bundle['private']}))
                status='candidate'
                if not task.get('continue'):
                    return
    finally:
        emit('done',status=status,cpu_seconds=0)


def unresponsive_worker(*_args):
    time.sleep(10)


class ParallelPoolTests(unittest.TestCase):
    def execute(self,bundle,workers=2,seconds=5,**kwargs):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'result'
            result=run_pool(fixture_worker,bundle,workers=workers,seconds=seconds,out=out,**kwargs)
            events=[json.loads(line) for line in (out/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            plan=json.loads((out/'FIRST_PLAN.json').read_text()) if (out/'FIRST_PLAN.json').exists() else None
            self.assertTrue(result['all_children_stopped'])
            self.assertFalse(result['forced_termination_workers'])
            return result,events,plan

    def test_slow_first_branch_does_not_block_second_worker(self):
        result,events,plan=self.execute({'tasks':[{'delay':2},{'delay':.02,'candidate':True}]})
        self.assertEqual(plan['task'],1)
        self.assertFalse(any(e['event']=='task_end' and e['task']==0 for e in events))
        self.assertEqual(result['winner_worker'],1)

    def test_worker_state_is_private_and_parent_unchanged(self):
        bundle={'tasks':[{'delay':.02,'candidate':True},{'delay':2}],'private':[]}
        _,_,plan=self.execute(bundle)
        self.assertEqual(plan['private'],[0])
        self.assertEqual(bundle['private'],[])

    def test_one_worker_keeps_inventory_order(self):
        _,events,plan=self.execute({'tasks':[{'delay':.02},{'delay':.01,'candidate':True}]},workers=1)
        self.assertEqual([e['task'] for e in events if e['event']=='task_start'],[0,1])
        self.assertEqual(plan['task'],1)

    def test_deadline_stops_owned_processes(self):
        result,_,plan=self.execute({'tasks':[{'delay':10},{'delay':10}]},seconds=.3)
        self.assertIsNone(plan)
        self.assertEqual(result['outcome'],'deadline_without_candidate')

    def test_failed_worker_does_not_hide_other_result(self):
        result,_,plan=self.execute({'tasks':[{'delay':0,'error':True},{'delay':.02,'candidate':True}]})
        self.assertEqual(plan['task'],1)
        self.assertTrue(any(r['status']=='error' for r in result['worker_reports']))

    def test_empty_inventory_is_not_a_legal_empty_plan(self):
        result,_,plan=self.execute({'tasks':[]})
        self.assertIsNone(plan)
        self.assertEqual(result['workers'],0)
        self.assertNotEqual(result['outcome'],'candidate')

    def test_existing_output_directory_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                run_pool(fixture_worker,{'tasks':[]},workers=1,seconds=1,out=directory)

    def test_unresponsive_owned_worker_is_reclaimed_and_marked_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            result=run_pool(unresponsive_worker,{'tasks':[{}]},workers=1,seconds=.3,
                            grace_seconds=.1,out=Path(directory)/'result')
            self.assertEqual(result['forced_termination_workers'],[0])
            self.assertTrue(result['all_children_stopped'])
            self.assertFalse(result['cpu_accounting_complete'])

    def test_replay_requires_exact_recorded_context(self):
        record={'event':'model_sets','context':{'day':1},'proposals':[[1]],'restaurants':[3]}
        model=RecordedModel([record])
        answer=model([{'content':'prompt\n{"day":1}'}])
        self.assertEqual(json.loads(answer)['sets'],[{'attractions':[1]}])
        with self.assertRaises(ReplayMiss):
            model([{'content':'prompt\n{"day":2}'}])

    def test_budget_continues_after_first_candidate(self):
        result,_,_=self.execute({'tasks':[{'delay':.01,'candidate':True,'continue':True},
                                          {'delay':.01,'candidate':True}]},workers=1,stop_mode='budget')
        self.assertEqual(result['system_unique_candidates'],2)
        self.assertEqual(result['stop_reason'],'inventory_finished_or_error')

    def test_target_counts_unique_candidates_globally(self):
        same={'same':True}
        tasks=[{'delay':.01,'candidate':True,'continue':True,'plan':same},
               {'delay':.01,'candidate':True,'continue':True,'plan':same},
               {'delay':.01,'candidate':True,'continue':True,'plan':{'other':True}},
               {'delay':10}]
        result,_,_=self.execute({'tasks':tasks},workers=1,stop_mode='target',candidate_limit=2)
        self.assertEqual(result['system_unique_candidates'],2)
        self.assertEqual(result['cross_worker_duplicates'],1)
        self.assertEqual(result['stop_reason'],'candidate_target')

    def test_both_processes_can_contribute_in_budget_mode(self):
        result,_,_=self.execute({'tasks':[{'delay':.01,'candidate':True},{'delay':.1,'candidate':True}]},stop_mode='budget')
        self.assertEqual(result['system_unique_candidates'],2)
        self.assertTrue(result['cpu_accounting_complete'])

    def test_restaurant_changes_do_not_inflate_attraction_sets(self):
        first={'itinerary':[{'activities':[{'type':'attraction','position':'A'},
                                           {'type':'dinner','position':'B'}]}]}
        second={'itinerary':[{'activities':[{'type':'attraction','position':'A'},
                                            {'type':'dinner','position':'C'}]}]}
        self.assertEqual(day_sets(first),day_sets(second))

    def test_pareto_does_not_inflate_equal_quality_vectors(self):
        quality={'reference_cost':100,'walk_minutes':10,'local_transit_minutes':20,'tour_minutes':90}
        rows=[{'fingerprint':'a','quality':quality},{'fingerprint':'b','quality':dict(quality)},
              {'fingerprint':'c','quality':{**quality,'reference_cost':110}}]
        self.assertEqual(pareto(rows),['a'])


if __name__=='__main__':
    unittest.main()
