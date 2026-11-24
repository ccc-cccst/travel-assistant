import tempfile
import unittest

from experiments.timing_probe.rank_replay import control_answer


class RankControlTests(unittest.TestCase):
    def test_identical_input_uses_same_answer_without_reusing_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            first,info=control_answer({'content':'IDList: [1,2]','other':1},'hotel_ranking','abc',folder)
            self.assertFalse(info['replayed'])
            second,info=control_answer({'content':'IDList: [2,1]','other':2},'hotel_ranking','abc',folder)
            self.assertEqual(first['content'],second['content'])
            self.assertEqual(second['other'],2)
            self.assertTrue(info['changed_generated_answer'])
            independent,_=control_answer({'content':'IDList: [3]'},'hotel_ranking','def',folder)
            self.assertNotEqual(first['content'],independent['content'])

    def test_uncontrolled_stages_and_default_are_unchanged(self):
        answer={'content':'reply'}
        self.assertEqual(control_answer(answer,'day_sets','abc',None),(answer,None))
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(control_answer(answer,'trip_candidate_recall','abc',folder),(answer,None))


if __name__=='__main__':unittest.main()
