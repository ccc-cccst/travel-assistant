"""Exercise TripSpec over the actual localhost HTTP routes with canned model replies."""
import json
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
P007=ROOT/'result/experiments/P007_tripspec_evaluation'


def main():
    from chinatravel.personalization import tripspec, web
    gold={r['record_no']:r for line in (P007/'GOLD_DRAFT.jsonl').read_text(encoding='utf-8').splitlines() if (r:=json.loads(line))}
    timeout_text, conflict_text=gold[15]['raw_text'],gold[132]['raw_text']
    actual=tripspec.interpret

    def response(text,_form,_cities):
        if text==timeout_text:
            return tripspec.ModelReply(error='APITimeoutError',attempts=1)
        assert text==conflict_text
        return tripspec.ModelReply(draft={'items':[],'questions':[]},attempts=1)

    tripspec.interpret=lambda payload,cities:actual(payload,cities,response)
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        sys.argv=['tripspec-http-test','--port',str(port)]
        thread=threading.Thread(target=web.main,daemon=True)
        thread.start()
        opener=build_opener(ProxyHandler({}))

        def send(path,body=None):
            req=Request(origin+path,data=None if body is None else json.dumps(body).encode(),
                        headers={'Content-Type':'application/json'})
            with opener.open(req,timeout=5) as res:
                text=res.read().decode('utf-8')
                return json.loads(text) if res.headers.get('Content-Type','').startswith('application/json') else text

        for _ in range(100):
            try:
                view=send('/tripspec')
                break
            except (URLError,ConnectionError,TimeoutError) as error:
                last_error=error
                time.sleep(.1)
        else:
            raise AssertionError('服务启动超时') from last_error
        assert '核对我的需求' in view
        assert isinstance(send('/options')['cities'],list)
        source=json.loads((ROOT/'examples/four_day_appointment.json').read_text(encoding='utf-8'))
        pure=send('/tripspec/interpret',source)
        assert pure['ready'] and pure['model']['attempts']==0
        assert any(a['field']=='appointment' and a['value']==source['appointments'][0] for a in pure['items'])
        snapshot=send('/facts',{'action':'trip_snapshot','spec':pure,'limit':1})
        assert snapshot['request_id']==pure['request_id'] and snapshot['request_revision']==pure['request_revision']
        assert snapshot['candidates']['attraction'][0]['name']=='上海博物馆'
        collision=send('/tripspec/interpret',{'raw_text':conflict_text})
        assert not collision['ready']
        try:
            send('/facts',collision)
        except HTTPError as error:
            assert error.code==400
        else:
            raise AssertionError('待确认的需求被事实快照入口接受')
        q=next(q for q in collision['issues'] if q['field']=='days')
        assert [a['value'] for a in q['choices']]==[5,7]
        submitted={'request_id':collision['request_id'],'request_revision':1,'choices':{q['id']:1}}
        revised=send('/tripspec/resolve',submitted)
        assert revised['ready'] and revised['request_revision']==2
        assert revised['raw_text']==conflict_text
        state=send('/tripspec/state?id='+collision['request_id'])
        assert state==revised
        try:
            send('/tripspec/resolve',submitted)
        except HTTPError as error:
            assert error.code==409
        else:
            raise AssertionError('旧版本的修改被错误接受')
        timed=send('/tripspec/interpret',{'raw_text':timeout_text,'form':{'budget':5000}})
        assert not timed['ready'] and timed['model']['error']=='APITimeoutError'
        assert any(x['field']=='budget' and x['value']==5000 for x in timed['items'])
        result={
            'mode':'actual_localhost_http_with_canned_model_replies',
            'page_served':True, 'form_without_api_and_appointment':True, 'trip_snapshot_http':True,
            'conflict_resolved_and_revision_reloaded':True, 'stale_revision_rejected':True,
            'timeout_kept_form':True, 'actual_cloud_calls':0,
        }
        print(json.dumps(result,ensure_ascii=False))
    finally:
        tripspec.interpret=actual


if __name__=='__main__':
    main()
