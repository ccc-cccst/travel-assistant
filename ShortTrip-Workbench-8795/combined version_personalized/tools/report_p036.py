"""Summarize all frozen submissions and make an offline adoption ballot."""
import argparse
from collections import Counter
from hashlib import sha256
from html import escape
import json
from math import ceil
from pathlib import Path
from statistics import median

from acceptance_p036 import OUT, read, save


def summarize(run):
    rows=read(run/'SUMMARY.json')['rows']
    done=sorted(r['first_complete_seconds'] for r in rows if r['outcome']=='original')
    models=[m for r in rows for m in (r.get('interpret_model'),r.get('sketch_model')) if m]
    summary=dict(submissions=len(rows),outcomes=dict(Counter(r['outcome'] for r in rows)),
                 by_level={level:dict(Counter(r['outcome'] for r in rows if r['level']==level))
                           for level in ('easy','medium','human')},
                 simulated=[dict(case=r['case'],**r['simulated_followup']) for r in rows if r.get('simulated_followup')],
                 initial_original_p50=median(done) if done else None,
                 initial_original_p95=done[ceil(.95*len(done))-1] if done else None,
                 model_calls=sum(m.get('requests',m.get('attempts',0)) for m in models),
                 tokens=sum(m.get('usage',{}).get('total_tokens',0) for m in models),
                 model_errors=dict(Counter(m['error'] for m in models if m.get('error'))))
    # A clarification can launch a second complete planning job; charge its usage too.
    for r in rows:
        path=run/r['case']/'SIMULATED_RESULT.json'
        if path.exists():
            m=read(path).get('model') or {}
            summary.setdefault('followup_model_calls',0)
            summary.setdefault('followup_tokens',0)
            summary['followup_model_calls']+=m.get('requests',0)
            summary['followup_tokens']+=m.get('usage',{}).get('total_tokens',0)
    return save(run/'METRICS.json',summary)


def ballot(run):
    rows=read(run/'SUMMARY.json')['rows']
    corpus={c['case']:c for c in read(OUT/'FROZEN30.json')['cases']}
    parts=[]
    mapping=[]
    for level in ('easy','medium','human'):
        pool=sorted((r for r in rows if r['level']==level and r['outcome']=='original'),
                    key=lambda r:sha256(('P036/adoption/'+r['case']).encode()).digest())
        for r in pool[:2]:
            n=len(parts)+1
            candidate=read(run/r['case']/'PLAN_VIEW.json')['candidates'][0]
            q=candidate['metrics']
            days=''.join('<p><b>第 '+str(d['day'])+' 天</b>　'+'、'.join(escape(a['name']) for a in d['activities'])
                         +'</p>' for d in candidate['days'])
            buttons=''.join(f'<label><input type="radio" name="r{n}" value="{s}"> {s}</label>'
                            for s in ('直接采用','改一处后采用','需要重做','资料不足'))
            raw=escape(corpus[r['case']]['raw_text'])
            job=r['job']; ident=read(run/r['case']/'request.json')['request_id']
            parts.append(f'<section id="q{n}"><h2>行程 {n}</h2><blockquote>{raw}</blockquote>{days}'
                         f'<p>整团参考 {q["total_cost_cny"]:,.2f} 元 · 市内接驳 {q["city_transit_minutes"]} 分钟'
                         f' · 接驳中的步行 {q["access_walk_minutes"]} 分钟 · 游览 {q["visit_minutes"]} 分钟</p>'
                         '<p>价格、库存和当天开放情况需出行前核实。</p>'
                         f'<a href="http://127.0.0.1:8780/tripspec?id={ident}&amp;job={job}" target="_blank">在本机助手查看或试换酒店</a>'
                         f'<fieldset><legend>如果是这位旅客，你会采用吗？</legend>{buttons}</fieldset>'
                         f'<label>最想改什么？实际修改后是否愿意采用？<textarea name="note{n}"></textarea></label></section>')
            mapping.append({'ballot':n,'case':r['case'],'job':job,'candidate_id':candidate['id']})
    save(run/'BALLOT_KEY.json',mapping)
    title='ShortTrip 行程采纳评审'
    document=('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
              f'<title>{title}</title><style>body{{font:17px/1.6 system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem;color:#182626}}'
              'section{padding:1rem 0;border-top:1px solid #bbb}blockquote{margin:1rem 0;padding:.7rem;background:#edf5f2}'
              'label{display:block;margin:.5rem 0}textarea{display:block;width:95%;min-height:3rem}button{padding:.7rem 1.2rem}</style>'
              f'<h1>{title}</h1><p>请独立评两份行程。日期内的地点按集合展示。若只想换酒店，可以在运行 ShortTrip 的电脑上点击查看。'
              '按自己的取舍回答，最后导出所选题目的匿名结果。</p><form>'+''.join(parts)+'</form>'
              '<button id="export">导出我的评审</button><p id="status"></p><script>'
              "document.querySelector('#export').onclick=()=>{const f=new FormData(document.querySelector('form'));"
              "const answers=Array.from(document.querySelectorAll('section'),(_,i)=>({ballot:i+1,choice:f.get('r'+(i+1)),"
              "note:f.get('note'+(i+1))})).filter(x=>x.choice);if(!answers.length){document.querySelector('#status').textContent='请先选择一项';return;}"
              "const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({answers},null,2)],{type:'application/json'}));"
              "a.download='ShortTrip-review-'+Date.now()+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};</script></html>")
    (run/'ADOPTION.html').write_text(document,encoding='utf8')


def check_raw_plans(plans, manifest):
    """Post-run desk review, independent of the planner's extracted constraint list."""
    core={c['case']:c['corpus_core'] for c in manifest['cases']}
    budgets={'human_7':2000,'human_99':2000,'medium_105':8500,'medium_128':6000,'medium_13':4600,
             'medium_19':4000,'medium_23':3000,'medium_24':7000,'medium_46':7500,'medium_57':6900,
             'medium_60':6100,'medium_80':4500}
    rooms={'medium_105':2,'medium_128':1,'medium_13':1,'medium_19':1,'medium_23':2,'medium_24':1,
           'medium_46':2,'medium_57':2,'medium_60':2,'medium_80':2}
    modes={'easy_263':['train','train'],'easy_278':['train','train'],'easy_299':['airplane','airplane'],
           'medium_24':['airplane','airplane'],'medium_80':['train','train'],'medium_105':['train',None]}
    hotels={'easy_164':'深圳大梅沙京基洲际度假酒店'}
    local={'easy_263':{'metro','walk'},'easy_278':{'taxi'}}
    checked=[]
    for case, plan in plans:
        activities=[a for d in plan['itinerary'] for a in d['activities']]
        c=core[case]
        tests=dict(origin=plan['start_city']==c['origin_city'],destination=plan['target_city']==c['destination_city'],
                   days=len(plan['itinerary'])==c['days'],party=plan['people_number']==c['people'])
        stays=[a for a in activities if a['type']=='accommodation']
        tickets=[a['type'] for a in activities if a['type'] in ('airplane','train')]
        if case in budgets:
            tests['budget']=sum(a['cost']+sum(t['cost'] for t in a['transports']) for a in activities)<=budgets[case]+.01
        if case in rooms:
            tests['rooms']=len(stays)==c['days']-1 and all(a['rooms']==rooms[case] for a in stays)
        if case in modes:
            tests['intercity']=len(tickets)==2 and all(not e or e==a for e,a in zip(modes[case],tickets))
        if case in hotels:
            tests['hotel']=len(stays)==c['days']-1 and all(a['position']==hotels[case] for a in stays)
        if case in local:
            tests['local_modes']=all(t['mode'] in local[case] for a in activities for t in a['transports'])
        checked.append(dict(case=case,checks=tests))
    return dict(checked=checked,checks=sum(len(c['checks']) for c in checked),
                failed=[(r['case'],k) for r in checked for k,v in r['checks'].items() if not v],
                scope='Desk-coded core fields and explicit caps, rooms, named lodging and transport on direct deliveries.')


def audit_raw(run):
    def original_plans():
        for row in read(run/'SUMMARY.json')['rows']:
            if row['outcome'] != 'original':
                continue
            folder = run / row['case']
            view = read(folder/'PLAN_VIEW.json')
            yield row['case'], next(c['itinerary'] for c in read(folder/'CANDIDATE_ARCHIVE.json')['archive']
                                    if c['id'] == view['recommended_id'])

    return save(run/'RAW_CHECKS.json', check_raw_plans(original_plans(), read(OUT/'FROZEN30.json')))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    print(json.dumps(summarize(args.run),ensure_ascii=False))
    print(audit_raw(args.run)['failed'])
    ballot(args.run)
