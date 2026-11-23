"""Audit first deliveries and prepare anonymous cards from the two workbench runs."""
from collections import Counter
import argparse
import csv
from math import ceil
from pathlib import Path
from random import Random
from statistics import median

from blind_candidate_review import card_text, evidence, sha
from compare_planning_regression import review_card
from report_p036 import check_raw_plans
from report_workbench import OUT, read, save
from chinatravel.personalization.cli import ROOT, fresh


EXPERIMENTS = ROOT / 'result/experiments'
CURRENT = EXPERIMENTS / 'P056_model_after_search/regression_01'
BASELINE = EXPERIMENTS / 'P054_release_online_pair/planning_01'
MANUAL = ROOT / 'result/manual'
DIMENSIONS = ('total_cost_cny', 'city_transit_minutes', 'access_walk_minutes', 'visit_minutes')


def card(folder, candidate):
    spec = read(folder / 'request.json')
    view = next(value for value in read(folder / 'PLAN_VIEW.json')['candidates'] if value['id'] == candidate)
    assessed = next(value for value in read(folder / 'PLAN_ASSESSMENT.json')['candidates'] if value['id'] == candidate)
    assert assessed['validation_report']['passed'], candidate
    content = review_card(folder, spec, view)
    for field in ('peak_daily_city_transit_minutes', 'distinct_attractions'):
        content['metrics'][field] = assessed['quality_vector'][field]
    content['goal_evidence'] = [evidence(goal) for goal in assessed['goals']]
    return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    frozen = read(OUT / 'FROZEN30.json')
    original_text = {item['case']: item['raw_text'] for item in frozen['cases']}
    previous = {row['case']: row for row in read(BASELINE / 'SUMMARY.json')}
    current = read(CURRENT / 'SUMMARY.json')
    args = parser.parse_args()
    rows, pairs, first_plans = [], [], []
    for row in current:
        if row['outcome'] != 'complete':
            continue
        name = row['case']
        old = previous[name]
        folder, old_folder = MANUAL / row['job'], MANUAL / old['job']
        archive = read(folder / 'CANDIDATE_ARCHIVE.json')
        trial = archive['model_alternative']
        first = read(folder / 'WORKBENCH_FIRST.json')['candidate']
        assert first['id'] == row['first_candidate']
        first_plans.append((name, first['itinerary']))
        old_best, best = card(old_folder, old['candidate']), card(folder, row['candidate'])
        summary = {'case':name, 'model_state':trial['state'], 'model_seconds':read(folder / 'STATUS.json')['timings']['model_alternative'],
                   'first_same':row['first_candidate'] == old['first_candidate'],
                   'recommended_same':row['candidate'] == old['candidate'],
                   'old_first_entire_seconds':old['interpret_s'] + old['first_s'],
                   'first_entire_seconds':row['interpret_s'] + row['first_s'],
                   'delta':{key:round(best['metrics'][key] - old_best['metrics'][key], 2) for key in DIMENSIONS}}
        if not summary['recommended_same']:
            pairs.append((name, '配置对照', old_best, best, old['candidate'], row['candidate']))
        if trial['state'] == 'added':
            alternative = card(folder, trial['candidate_id'])
            summary['model_delta'] = {key:round(alternative['metrics'][key] - best['metrics'][key], 2) for key in DIMENSIONS}
            pairs.append((name, '模型新增备选', best, alternative, row['candidate'], trial['candidate_id']))
        rows.append(summary)

    audit = check_raw_plans(first_plans, frozen)
    out = fresh(args.out)
    save(out / 'FIRST_RAW_CHECKS.json', audit)
    assert len(first_plans) == 26 and not audit['failed']
    times = sorted(row['first_entire_seconds'] for row in rows)
    save(out / 'ANALYSIS.json', {'first_entire_seconds':{'p50':median(times),'p90':times[ceil(.9 * len(times)) - 1]},
                                'model_outcomes':dict(Counter(row['model_state'] for row in rows)), 'cases':rows})

    rng = Random(2026112256)
    rng.shuffle(pairs)
    sides = ['A', 'B'] * (len(pairs) // 2) + ['A'] * (len(pairs) % 2)
    rng.shuffle(sides)
    cards, key = [], {}
    for number, ((case, group, first, second, first_id, second_id), side) in enumerate(zip(pairs, sides), 1):
        pair = str(number).zfill(2)
        other = 'B' if side == 'A' else 'A'
        cards.append({'id':pair, 'request':original_text[case], side:first, other:second})
        key[pair] = {'case':case, 'type':group, 'primary':side, 'ids':{side:first_id, other:second_id}}
    save(out / 'CARDS.json', cards)
    save(out / 'KEY.json', key)
    save(out / 'MANIFEST.json', {'cards_sha256':sha(out / 'CARDS.json')})
    lines = ['# 匿名行程比较', '', '请选择你愿意采用的方案。可以选 A、B、差不多、都不采用或资料不足，并写一句理由。',
             '两份行程都通过旅行快照核验。整团费用为参考，步行仅包括接驳。出行日期的票房和营业仍需确认。', '']
    for pair in cards:
        lines.extend([f"## {pair['id']}", '', pair['request'], ''])
        for side in ('A', 'B'):
            lines.extend([*card_text(side, pair[side]), ''])
    (out / 'BLIND_REVIEW.md').write_text('\n'.join(lines), encoding='utf8')
    with (out / 'BALLOT_TEMPLATE.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(('pair', 'choice', 'reason'))
        writer.writerows((card['id'], '', '') for card in cards)
    print(f'{len(first_plans)} first plans, {audit["checks"]} independent checks, {len(cards)} anonymous pairs')


if __name__ == '__main__':
    main()
