import unittest
from types import SimpleNamespace

from experiments.timing_probe.combo_schedule import dispatch, precheck, combination_agent


class DispatchTests(unittest.TestCase):
    def test_progressing_root_can_finish_while_new_roots_keep_arriving(self):
        from itertools import count
        visited, completed = [], []
        def trials(name):
            for step in range(6):
                visited.append((name, step))
                yield {'progress': step // 2}
            completed.append(name)
        def deadline():
            if completed or len(visited) >= 32:
                raise RuntimeError('bounded observation')
        with self.assertRaisesRegex(RuntimeError, 'bounded observation'):
            dispatch(count(), trials, deadline, lambda _event: None)
        self.assertTrue(completed, 'fresh candidates must not starve an almost-complete root')

    def test_return_only_day_does_not_request_places_from_model(self):
        import pandas as pd
        class Base:
            def __init__(self, **kwargs):
                self.backbone_llm = SimpleNamespace(_send_request=lambda *_a: self.fail_model())
                self.query = {'days': 2, 'nature_language': '两天出行'}
                self.memory = {'attractions': pd.DataFrame([{'name': '博物馆'}])}
                self.attraction_names_visiting = []
                self.timing_policy = SimpleNamespace(attraction_start=360, attraction_end=1380)
            def fail_model(self):
                raise AssertionError('return-only day must not call the model')
        agent = combination_agent(Base, lambda _a: None)()
        root = {'back_transport': {'TrainID': 'G1', 'BeginTime': '06:41'}}
        self.assertEqual(agent.build_day_options(1, '00:00', '酒店', root), [()])
        agent.query['appointments'] = [{'name': '博物馆', 'day': 2, 'start_time': '15:00'}]
        self.assertEqual(agent.build_day_options(1, '00:00', '酒店', root), [])

    def test_unknown_ticket_date_is_not_declared_infeasible(self):
        self.assertIsNone(precheck({'days': 2}, {'go_transport': {'BeginTime': 'unknown'}, 'back_transport': {}}))

    def test_early_return_is_allowed_but_impossible_appointment_is_rejected(self):
        query = {'days': 2, 'people_number': 1}
        root = {'go_transport': {'BeginTime': '08:00', 'EndTime': '12:00', 'Duration': 4},
                'back_transport': {'BeginTime': '06:41', 'EndTime': '10:41', 'Duration': 4}}
        self.assertIsNone(precheck(query, root))
        query['appointments'] = [{'day': 2, 'name': '博物馆', 'start_time': '15:00', 'duration_minutes': 90}]
        self.assertEqual(precheck(query, root)['rule'], 'appointment_outside_trip')

    def test_stuck_early_combination_yields_and_resumes_without_restarting(self):
        visited = []
        closed = []

        def trials(name):
            try:
                for step in range(8):
                    visited.append((name, step))
                    yield {'progress': step}
            finally:
                closed.append(name)

        def deadline():
            if ('B', 1) in visited:
                raise RuntimeError('stop after observed progress')

        events = []
        with self.assertRaisesRegex(RuntimeError, 'observed progress'):
            dispatch(iter(['A', 'B', 'C']), trials, deadline, events.append)
        self.assertIn(('A', 0), visited)
        self.assertIn(('B', 1), visited)
        self.assertEqual(visited.count(('A', 0)), 1)
        self.assertEqual(set(closed), {name for name, _ in visited})


if __name__ == '__main__':
    unittest.main()
