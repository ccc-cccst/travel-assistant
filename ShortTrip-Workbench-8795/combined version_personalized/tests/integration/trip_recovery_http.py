"""Exercise recovery and saved versions through the real HTTP worker."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chinatravel.personalization.cli import read, save


def main():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = dict(os.environ)
    env.pop('DEEPSEEK_API_KEY', None)
    server = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', '-m', 'chinatravel.personalization.web',
                               '--port', str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL)
    opener = build_opener(ProxyHandler({}))

    def send(path, body=None):
        request = Request(f'http://127.0.0.1:{port}'+path, data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        with opener.open(request, timeout=15) as reply:
            value = reply.read().decode('utf-8')
            return json.loads(value) if 'application/json' in reply.headers['Content-Type'] else value

    def wait(callback, timeout=60):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                if result := callback():
                    return result
            except HTTPError:
                raise
            except (URLError, ConnectionError) as error:
                last_error = error
                if server.poll() is not None:
                    raise
            time.sleep(.25)
        raise RuntimeError('在时限内没有取得预期的网页任务状态') from last_error

    def start(case):
        source = ROOT/'result/experiments/P031_first_plan/after_02'/case/'request.json'
        spec = {**read(source), 'request_id': uuid.uuid4().hex}
        directory = ROOT/'result/tripspec'/spec['request_id']
        save(directory/'latest.json', spec)
        save(directory/f"revision_{spec['request_revision']}.json", spec)
        job = send('/trip-planning', {k: spec[k] for k in ('request_id', 'request_revision')})
        return spec, directory, job['id']

    def status(job):
        return send('/trip-planning/job?id='+job)

    def recovery_ready(job):
        state = status(job)
        return state if state.get('recovery', {}).get('state') == 'complete' else None

    try:
        page = wait(lambda: send('/tripspec'))
        assert '调整后的完整行程' in page and '接受这项调整' in page
        spec, folder, job = start('human1000_666')
        first = wait(lambda: recovery_ready(job))
        assert first['state'] == 'no_eligible_intercity_transport'
        adjustment, = [a for a in first['recovery']['attempts'] if a.get('view')]
        assert adjustment['change']['after'] == '火车'
        assert read(folder/'latest.json') == spec
        preview_route = {'job_id': job, 'attempt_id': adjustment['id'],
                         'candidate_id': adjustment['view']['recommended_id'], 'day': 2}
        for view_only in (True,False):
            route_job = send('/trip-planning/route', {**preview_route,'view_only':view_only})['id']
            result = wait(lambda: status(route_job).get('route'))
            assert result['day']==2 and result['candidate_id']==preview_route['candidate_id']
            assert result['segments'] and result['model_calls']==0
        assert read(folder/'latest.json') == spec and not status(job).get('result')
        for broken in ({'attempt_id':'../attempt_1'},{'candidate_id':'unverified'}):
            try:
                send('/trip-planning/route',{**preview_route,**broken})
            except HTTPError as error:
                assert error.code==400
            else:
                raise AssertionError('非法的待确认候选启动了路线计算')
        accept = {'job_id': job, 'attempt_id': adjustment['id']}
        accepted = send('/trip-planning/recovery/accept', accept)
        assert accepted['request_revision'] == spec['request_revision'] + 1
        assert send('/trip-planning/recovery/accept', accept) == accepted
        assert send('/tripspec/state?id='+spec['request_id']) == accepted
        adopted = status(job)
        assert adopted['state'] == 'complete' and adopted['result'] == adjustment['view']
        assert adopted['request_revision'] == accepted['request_revision']
        route = send('/trip-planning/route', {'job_id': job, 'candidate_id': adopted['result']['recommended_id'],
                                            'day': 2, 'view_only': True})['id']
        assert wait(lambda: status(route).get('route'))['day'] == 2

        conflict, _, conflict_job = start('human_9')
        options = wait(lambda: recovery_ready(conflict_job))['recovery']['attempts']
        preview = next(a for a in options if a.get('view'))
        question = conflict['issues'][0]
        send('/tripspec/resolve', {'request_id': conflict['request_id'],
                                 'request_revision': conflict['request_revision'], 'choices': {question['id']: 1}})
        try:
            send('/trip-planning/route', {'job_id':conflict_job,'attempt_id':preview['id'],
                 'candidate_id':preview['view']['recommended_id'],'day':1})
        except HTTPError as error:
            assert error.code==409
        else:
            raise AssertionError('旧版本的待确认路线被接受')
        try:
            send('/trip-planning/recovery/accept', {'job_id': conflict_job, 'attempt_id': preview['id']})
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError('旧需求的方案被错误采用')

        _, _, draft_job = start('easy_24')
        partial = wait(lambda: recovery_ready(draft_job))
        assert partial['recovery']['draft']['stops'] and not partial.get('result')
        print(json.dumps({'accepted_job': job, 'conflict_job': conflict_job, 'draft_job': draft_job,
                          'idempotent_accept': True, 'reloaded': True, 'route_verified': True,
                          'route_preview_and_sort_verified':True,'preview_did_not_adopt':True,
                          'stale_revision_rejected': True, 'draft_published': True}, ensure_ascii=False))
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
    main()
