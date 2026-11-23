"""Prepare anonymous paired itinerary cards or score a completed human ballot."""
import argparse
from collections import Counter
import csv
from hashlib import sha256
from pathlib import Path
from random import Random
import sys

from compare_planning_regression import LABELS, PENDING_LABELS, review_card
from chinatravel.personalization.cli import ROOT, fresh, read, save
from chinatravel.personalization.plan_assessment import select_candidates


ARCHIVES = ROOT/'result/experiments/P029_generalization/paired_01'
CASES = ('confirmed/human_8','confirmed/human1000_367','confirmed/human1000_641','model_replay/human_37')
SEED = 2026112130
CHOICES = ('A','B','差不多','都不采用','资料不足')
FIELDS = {'cuisine':'菜系','experience_theme':'景点主题','food_quality':'餐饮体验','pace':'节奏'}
EVIDENCE = {'unmet':'已排地点缺少匹配标签','insufficient_evidence':'现有资料无法核实'}


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def card_text(label, card):
    q = card['metrics']
    lines = [f'### {label}', '',
             f"整团参考价 {q['total_cost_cny']:,.2f} 元，市内接驳 {q['city_transit_minutes']} 分钟"
             f"（其中步行 {q['access_walk_minutes']} 分钟），景点游览 {q['visit_minutes']} 分钟"
             f"，共 {q['distinct_attractions']} 个不同景点。"
             f"单日接驳最多 {q['peak_daily_city_transit_minutes']} 分钟，最长一段 {q['longest_transfer_minutes']} 分钟。", '']
    for d in card['days']:
        activities = []
        for a in d['activities']:
            s = f"{LABELS.get(a['kind'],a['kind'])} {a['name']}"
            if a.get('time'):
                s += ' '+a['time']
            if a.get('incoming_minutes',0)>=30:
                s += f"（前段接驳 {a['incoming_minutes']} 分钟）"
            activities.append(s)
        lines.append(f"- 第 {d['day']} 天　"+' → '.join(activities))
    if card['goal_evidence']:
        lines += ['', '与需求有关的证据', '']
        for g in card['goal_evidence']:
            lines.append(f"- {g}")
    return lines


def evidence(goal):
    label = f"{FIELDS.get(goal['field'],goal['field'])}「{goal['value']}」"
    names = list(dict.fromkeys(p['name'] for p in goal['matched_places']))
    if goal['status']=='matched':
        return label+'　匹配地点包括'+'、'.join(names[:3])
    if goal['metric']=='peak_daily_city_transit_minutes':
        return label+f"　最忙一天合计接驳 {goal['observed']} 分钟"
    return label+'　'+EVIDENCE.get(goal['status'],goal['status'])


def prepare(out):
    rng = Random(SEED)
    current_side = ['A','A','B','B']
    rng.shuffle(current_side)
    index, manifest, cards = {}, {}, []
    header = ['# 四份旅行需求的匿名比较','',
              '请按每份需求选你愿意实际采用的方案。A、B、差不多、都不采用、资料不足均可，可附一句理由。',
              '参考价按全团计算。市内接驳含进出城车站或机场的接送，步行只计地点之间，游览只计在景点的时间。',
              '两套行程均已通过当时的快照核验。出行日期的价格、营业、余票、实际房型与入住人数仍需核实。','']
    for n, (case, side) in enumerate(zip(CASES,current_side),1):
        folder = ARCHIVES/case/'on'
        spec = read(folder/'request.json')
        ass = read(folder/'PLAN_ASSESSMENT.json')
        view = read(folder/'PLAN_VIEW.json')
        legal = {c['id']:c for c in ass['candidates'] if c['validation_report']['passed']}
        replay = select_candidates(list(legal.values()))
        assert replay == ass['selection']
        chosen = [replay['recommended_id'],replay['alternatives'][0]['id']]
        assert chosen==[c['id'] for c in view['candidates'][:2]] and len(set(chosen))==2
        labels = (side, 'B' if side=='A' else 'A')
        pair = f'{n:02d}'
        city = next(i['value'] for i in spec['items'] if i['field']=='destination_city')
        index[pair] = {'case':case,'city':city,'primary':side,'ids':dict(zip(labels,chosen))}
        manifest[case] = {f:sha(folder/f) for f in ('request.json','CANDIDATE_ARCHIVE.json','PLAN_VIEW.json','PLAN_ASSESSMENT.json')}
        public = {'id':pair,'city':city,'request':spec['raw_text']}
        for i,label in enumerate(labels):
            checked = legal[chosen[i]]
            v = review_card(folder,spec,view['candidates'][i])
            for k in ('peak_daily_city_transit_minutes','distinct_attractions'):
                v['metrics'][k] = checked['quality_vector'][k]
            public[label] = {**v,'goal_evidence':[evidence(g) for g in checked['goals']]}
        cards.append(public)
    out = fresh(out)
    lines = list(header)
    for p in cards:
        section = [f"## {p['id']}　{p['city']}",'',p['request'],'']
        for label in ('A','B'):
            section += card_text(label,p[label])+['']
        lines += section
        (out/f"CARD_{p['id']}.md").write_text('\n'.join(header+section),encoding='utf8')
    (out/'BLIND_REVIEW.md').write_text('\n'.join(lines),encoding='utf8')
    save(out/'CARDS.json',cards)
    save(out/'KEY.json',index)
    manifest_out = {'seed':SEED,'arm':'on','slots':[0,1],'source_sha256':manifest,
                    'ranking_source_sha256':sha(ROOT/'chinatravel/personalization/plan_assessment.py'),
                    'tool_sha256':sha(Path(__file__)),'cards_sha256':sha(out/'CARDS.json')}
    others = sorted((d for d in (ARCHIVES/'independent').iterdir() if (d/'on/PLAN_VIEW.json').is_file()),
                    key=lambda d:sha256(('P030-reserve/'+d.name).encode()).hexdigest())
    reserve = []
    for d in others:
        v = read(d/'on/PLAN_VIEW.json')
        if len(v['candidates'])>=2:
            reserve.append({'case':d.name,'view_sha256':sha(d/'on/PLAN_VIEW.json'),
                            'archive_sha256':sha(d/'on/CANDIDATE_ARCHIVE.json'),
                            'ids':[c['id'] for c in v['candidates'][:2]]})
        if len(reserve)==2:
            break
    assert len(reserve)==2
    save(out/'MANIFEST.json',{**manifest_out,'reserved':reserve})
    with (out/'BALLOT_TEMPLATE.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('pair','choice','reason'))
        writer.writerows((p['id'],'','') for p in cards)


def score(folder, ballot, exposure, out):
    key = read(folder/'KEY.json')
    if sha(folder/'CARDS.json')!=read(folder/'MANIFEST.json')['cards_sha256']:
        raise ValueError('行程卡与冻结版本不一致')
    with ballot.open(encoding='utf-8-sig',newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows)!=len(key) or {r['pair'] for r in rows}!=set(key):
        raise ValueError('每个题号须恰有一行，暂时未答可留空')
    counts = Counter()
    answers = []
    for r in rows:
        pair,choice = r['pair'],r['choice'].strip()
        if choice and choice not in CHOICES:
            raise ValueError(f'{pair} 的选项无法识别')
        result = ('当前首推' if choice==key[pair]['primary'] else '当前备选') if choice in ('A','B') else choice or '未答'
        counts[result]+=1
        answers.append({'pair':pair,'choice':choice,'reason':r['reason'],'result':result})
    decided = counts['当前首推']+counts['当前备选']
    save(fresh(out)/'VOTE_RESULT.json',{'exposure':exposure,'answers':answers,'counts':dict(counts),
         'decided':decided,'first_choice_agreement':counts['当前首推']/decided if decided else None,
         'ballot_sha256':sha(ballot),'cards_sha256':sha(folder/'CARDS.json')})


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    commands=cli.add_subparsers(dest='action',required=True)
    prep=commands.add_parser('prepare')
    prep.add_argument('--out',type=Path,required=True)
    votes=commands.add_parser('score')
    for name in ('folder','ballot','out'):
        votes.add_argument('--'+name,type=Path,required=True)
    votes.add_argument('--exposure',choices=('prior_results_seen','new_reviewer','unknown'),required=True)
    args=cli.parse_args()
    if args.action=='prepare':
        prepare(args.out)
    else:
        score(args.folder,args.ballot,args.exposure,args.out)


if __name__=='__main__':
    main()
