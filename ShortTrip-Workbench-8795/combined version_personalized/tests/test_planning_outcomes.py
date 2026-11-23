import unittest
from types import SimpleNamespace

from chinatravel.personalization.outcomes import transport_precheck, failure_outcome


class OutcomeTests(unittest.TestCase):
    def request(self):
        return {'trip':{'origin_city':'成都','destination_city':'苏州'},
                'facts':{'budget':None,'intercity_modes':['airplane']},'directional_modes':{}}

    def env(self,counts):
        def select(a,b,mode):
            value=counts.get((a,b,mode),0)
            if value is None: raise ValueError('unknown snapshot')
            return [None]*value
        return SimpleNamespace(intercitytransport=SimpleNamespace(select=select))

    def test_no_flights_reports_transport_not_money(self):
        env=self.env({('成都','苏州','train'):9,('苏州','成都','train'):15})
        result=transport_precheck(self.request(),env)
        self.assertEqual(result['state'],'transport_unavailable')
        self.assertIn('只坐飞机',result['message'])
        self.assertIn('只坐火车',result['message'])
        self.assertNotIn('预算',result['message'])
        self.assertIsNone(self.request()['facts']['budget'])

    def test_unlimited_mode_can_use_trains(self):
        request=self.request();request['facts']['intercity_modes']=['train','airplane']
        result=transport_precheck(request,self.env({('成都','苏州','train'):9,('苏州','成都','train'):15}))
        self.assertIsNone(result)

    def test_unknown_transport_is_not_proof_of_absence(self):
        env=self.env({('成都','苏州','airplane'):None,('苏州','成都','airplane'):1})
        self.assertIsNone(transport_precheck(self.request(),env))

    def test_timeout_is_named_time_limit(self):
        result=failure_outcome({'stop_reason':'matched_wall_budget'},0)
        self.assertEqual(result['state'],'search_timeout')
        self.assertIn('时间',result['message'])
        self.assertNotIn('费用',result['message'])

    def test_runtime_error_not_relabelled_no_solution(self):
        result=failure_outcome({'stop_reason':'error','error':{'type':'ValueError'}},1)
        self.assertEqual(result['state'],'worker_failed')

    def test_exhausted_does_not_claim_over_budget(self):
        result=failure_outcome({'stop_reason':'search_exhausted'},0)
        self.assertEqual(result['state'],'search_exhausted')
        self.assertNotIn('预算',result['message'])

    def test_candidate_audit_failure_has_distinct_message(self):
        result=failure_outcome({'stop_reason':'candidate_limit_reached'},0,candidate_count=3)
        self.assertEqual(result['state'],'verification_failed')
        self.assertIn('核验',result['message'])

    def test_unknown_stop_reason_is_not_claimed_exhausted(self):
        self.assertEqual(failure_outcome({},0)['state'],'no_verified_result')

    def test_api_limit_does_not_mean_travel_budget_limit(self):
        result=failure_outcome({'error':{'type':'RateLimitError'}},1)
        self.assertEqual(result['reason_code'],'model_rate_limited')
        self.assertNotIn('预算',result['message'])


if __name__=='__main__':unittest.main()
