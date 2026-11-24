"""Replay P036 inputs and record each change in the planning adapter."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import audit_scoped_budget_and_regions as replay
from chinatravel.personalization.cli import ROOT, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.tripspec import ModelReply, interpret, normalize_saved_spec, resolve_conflicts


BASELINE = ROOT/'result/experiments/P036_delivery_adoption/live_02'


def run(task):
    row, output, confirmed = task
    case = row['case']
    source = BASELINE/case
    spec = normalize_saved_spec(read(source/'request.json'))
    old = source/'STATUS.json'
    if confirmed:
        steps = row['simulated_followup']
        spec = resolve_conflicts(spec, steps['choices'])
        old = source/'SIMULATED_RESULT.json'
    baseline = read(old)
    reply = replay.read_model_layouts(source)
    snapshot = replay.facts.trip_snapshot(spec) if spec['ready'] else None
    layouts = propose_trip_sketches(snapshot, use_model=reply.draft is not None,
                                   provider=lambda *a, **kw: reply) if snapshot else None
    folder = output/(case+'_confirmed' if confirmed else case)
    current = run_trip_plan(spec, folder, replay.env, replay.facts,
                            saved_snapshot=snapshot, saved_layouts=layouts)
    compare = {'case': case, 'after_user_choice': confirmed, 'before': baseline['state'],
               'after': current['state'], 'delivery_status': current.get('delivery_status'),
               'seconds': current['elapsed_seconds'], 'cached_model_reply': reply.draft is not None}
    if current['state']=='complete':
        view = read(folder/'PLAN_VIEW.json')
        card = view['candidates'][0]
        verified = read(folder/'PLAN_ASSESSMENT.json')
        selected = next(c for c in verified['candidates'] if c['id'] == card['id'])
        assert selected['validation_report']['passed']
        assert all(check['status']=='passed' for check in selected['validation_report']['hard_requirements'])
        compare.update(candidate=card['id'], goals=card['goals'], metrics=card['metrics'],
                       hard_checks=len(selected['validation_report']['hard_requirements']))
    save(folder/'COMPARISON.json', compare)
    print(folder.name, compare['before'], compare['after'], flush=True)
    return compare


def contrast_cases(output):
    replay.init_world()
    tests = [
        ('wuhan_technology', '武汉', '想要多逛一些科技馆', '科技馆', 'fact', 'complete'),
        ('hangzhou_art', '杭州', '此行主要就是为了多逛美术馆', '美术馆', 'soft', 'complete'),
        ('shanghai_books', '上海', '此行主要就是为了多逛露营装备二手寄卖店', '露营装备二手寄卖店',
         'soft', 'core_experience_pending'),
        ('shanghai_minimum', '上海', '必须逛至少两家露营装备二手寄卖店', '露营装备二手寄卖店',
         'hard', 'planning_constraint_unsupported'),
    ]
    results=[]
    for case, destination, quote, topic, strength, expected in tests:
        extracted = dict(f='experience_example', op='eq', k=strength, s='local_experience', v=topic, q=quote)
        spec = interpret({'raw_text':quote,'form':dict(start_city='南京',target_city=destination, days=3,
                                                     people_number=2, budget=4000)},
                         replay.env.support_cities,
                         provider=lambda *a:ModelReply(draft={'items':[extracted],'questions':[]}))
        folder = output/case
        status = run_trip_plan(spec,folder,replay.env,replay.facts)
        result = status.get('delivery_status') or status['state']
        entry = dict(case=case, original=quote, expected=expected, result=result)
        view = folder/'PLAN_VIEW.json'
        if view.is_file():
            entry['goals']=read(view)['candidates'][0]['goals']
        results.append(entry)
        save(output/'SUMMARY.json',results)
        print(case,expected,result,flush=True)
    if any(r['result']!=r['expected'] for r in results):
        raise SystemExit('A contrast did not meet its expected delivery state')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--targeted', action='store_true')
    parser.add_argument('--contrasts', action='store_true')
    args = parser.parse_args()
    output = fresh(args.out)
    if args.contrasts:
        contrast_cases(output)
        raise SystemExit()
    rows = read(BASELINE/'SUMMARY.json')['rows']
    tasks = [(row, output, False) for row in rows if not args.targeted or row['case']=='easy_174']
    tasks.extend((row, output, True) for row in rows if row.get('simulated_followup', {}).get(
        'action')=='choose_benchmark_core' and (not args.targeted or row['case']=='human_59'))
    with ProcessPoolExecutor(max_workers=3, initializer=replay.init_world) as pool:
        save(output/'SUMMARY.json', list(pool.map(run, tasks)))
