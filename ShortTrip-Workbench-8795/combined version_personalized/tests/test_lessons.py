import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from chinatravel.personalization.lessons import LessonBook, record_failure, describe_exception


class LessonTests(unittest.TestCase):
    def request(self):
        return {'request_id':'case','trip':{'origin_city':'成都','destination_city':'苏州','days':4,'people':2},
                'facts':{'budget':None,'intercity_modes':['airplane']},'directional_modes':{},'locked_events':[]}

    def env(self,unknown=False):
        def select(a,b,mode):
            if unknown and mode=='airplane':raise ValueError('incomplete evidence')
            return [] if mode=='airplane' else [1]
        return SimpleNamespace(intercitytransport=SimpleNamespace(select=select))

    def test_rulebook_is_read_and_applied_to_current_evidence(self):
        book=LessonBook()
        report=book.check(self.request(),self.env())
        self.assertEqual(report['blocking']['reason_code'],'no_eligible_intercity_transport')
        self.assertIn('L002',report['blocking']['lesson_ids'])
        self.assertTrue(report['rules_sha256'])

    def test_missing_evidence_does_not_blacklist_city_pair(self):
        result=LessonBook().check(self.request(),self.env(unknown=True))
        self.assertIsNone(result['blocking'])
        self.assertEqual(next(r['status'] for r in result['checks'] if r['lesson_id']=='L002'),'uncertain_no_block')
        req=self.request();req['facts']['intercity_modes']=['train','airplane']
        self.assertIsNone(LessonBook().check(req,self.env())['blocking'])

    def test_unreviewed_rule_does_not_execute(self):
        with TemporaryDirectory() as tmp:
            source=json.loads(LessonBook().path.read_text(encoding='utf-8'))
            for r in source['rules']:r['review_status']='proposed'
            path=Path(tmp)/'rules.json';path.write_text(json.dumps(source),encoding='utf-8')
            report=LessonBook(path).check(self.request(),self.env())
            self.assertIsNone(report['blocking'])
            self.assertTrue(all(r['status']=='skipped_unreviewed' for r in report['checks']))

    def test_unknown_handler_cannot_be_executed_as_code(self):
        with TemporaryDirectory() as tmp:
            source=json.loads(LessonBook().path.read_text(encoding='utf-8'))
            source['rules'][0]['handler']='__import__("os").system("anything")'
            path=Path(tmp)/'rules.json';path.write_text(json.dumps(source),encoding='utf-8')
            with self.assertRaises(ValueError):LessonBook(path)

    def test_incidents_are_idempotent_per_failure_and_secrets_redacted(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'result/manual/test';run.mkdir(parents=True)
            key='sk-'+'a'*32
            status={'state':'worker_failed','reason_code':'worker_failed','message':'模型请求失败',
                    'evidence':{'api_key':key,'detail':'Authorization: Bearer '+key}}
            first=record_failure(run,status,self.request(),root=root)
            second=record_failure(run,status,self.request(),root=root)
            self.assertEqual(first['incident_id'],second['incident_id'])
            self.assertEqual(len(list((root/'result/incidents').glob('*.json'))),1)
            text=(run/'ERROR.json').read_text(encoding='utf-8')
            self.assertNotIn(key,text)
            self.assertEqual(json.loads(text)['request']['money_budget'],None)
            self.assertEqual(first['diagnosis_status'],'undetermined')

    def test_new_failure_writes_new_occurrence_without_overwriting_old(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'result/manual/test';run.mkdir(parents=True)
            a=record_failure(run,{'state':'search_timeout','reason_code':'search_timeout','message':'规划时间到限'},root=root)
            old=(root/a['incident_file']).read_bytes()
            b=record_failure(run,{'state':'worker_failed','reason_code':'worker_failed','message':'运行异常'},root=root)
            self.assertNotEqual(a['incident_id'],b['incident_id'])
            self.assertEqual((root/a['incident_file']).read_bytes(),old)

    def test_timeout_is_not_a_confirmed_money_cause(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'run';run.mkdir()
            d=record_failure(run,{'state':'search_timeout','reason_code':'search_timeout','message':'规划时间已用尽'},self.request(),root=root)
            self.assertEqual(d['category'],'search_limit')
            self.assertEqual(d['diagnosis_status'],'undetermined')
            self.assertEqual(d['review_status'],'needs_review')
            self.assertIn('L001',d['lesson_ids'])

    def test_data_setup_exception_preserves_precise_stage(self):
        result=describe_exception(FileNotFoundError('missing snapshot'),'data_setup')
        self.assertEqual(result['reason_code'],'data_snapshot_unavailable')
        self.assertEqual(result['stage'],'data_setup')

    def test_model_setup_policy_is_loaded_from_book(self):
        opts=LessonBook().model_client_options()
        self.assertEqual(opts['default_headers']['Accept-Encoding'],'identity')

    def test_worker_can_read_exact_frozen_rule_snapshot(self):
        import os
        from unittest.mock import patch
        book=LessonBook()
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'lesson_rules_snapshot.json';path.write_bytes(book.raw)
            with patch.dict(os.environ,{'SHORTTRIP_LESSON_RULES':str(path)}):
                frozen=LessonBook()
                self.assertEqual(frozen.sha256,book.sha256)
                self.assertEqual(frozen.model_client_options(),book.model_client_options())

    def test_public_status_links_incident_and_redacts_secret(self):
        from unittest.mock import patch
        from chinatravel.personalization import cli
        with TemporaryDirectory() as tmp:
            root=Path(tmp);status=root/'result/manual/status/STATUS.json'
            key='sk-'+'b'*32
            with patch.object(cli,'ROOT',root):
                cli.save(status,{'state':'worker_failed','reason_code':'worker_failed',
                                 'message':'运行异常','evidence':{'api_key':key,'detail':key}})
            text=status.read_text(encoding='utf-8');saved=json.loads(text)
            self.assertNotIn(key,text)
            self.assertEqual(saved['incident_id'],saved['diagnostic']['incident_id'])
            self.assertTrue((root/saved['diagnostic']['incident_file']).exists())


if __name__=='__main__':unittest.main()
