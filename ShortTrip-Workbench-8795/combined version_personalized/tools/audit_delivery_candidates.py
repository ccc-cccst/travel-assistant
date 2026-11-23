"""Try a dinner-centered construction direction and budget alternatives with the stock planner."""
import argparse
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import ROOT, DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.trip_recovery import run_trip_with_recovery
from chinatravel.personalization.world_facts import WorldFacts
from chinatravel.personalization.planning_compat import constructor_input, prepare_snapshot_routes
from chinatravel.planning.engine import Constructor


EXP = ROOT/'result/experiments'
P031 = EXP/'P031_first_plan/after_02'
P033 = EXP/'P033_recovery/regression_01'


def outcome(folder, status):
    result = {'status': status['state'], 'message': status['message'],
              'seconds': status['elapsed_seconds'], 'first_seconds': status.get('first_visible_seconds')}
    if status['state'] == 'complete':
        view = read(folder/'PLAN_VIEW.json')
        checked = read(folder/'PLAN_ASSESSMENT.json')
        candidate, = [c for c in checked['candidates'] if c['id'] == view['recommended_id']]
        assert candidate['validation_report']['passed']
        assert all(h['status'] == 'passed' for h in candidate['validation_report']['hard_requirements'])
        result.update(verified=view['verified_count'], pick=view['recommended_id'],
                      metrics=view['candidates'][0]['metrics'], goals=view['candidates'][0]['goals'])
    return result


def quality(output, env, facts):
    for case in ('easy_114','medium_150','human_83'):
        spec = read(P033/case/'request.json')
        snapshot = facts.trip_snapshot(spec)
        new = propose_trip_sketches(snapshot)
        old = {**new, 'sketches': [s for s in new['sketches'] if s['source'] != 'nearby_named_meal']}
        row = {'case':case, 'triggered':new!=old}
        for label, layouts in [('baseline',old),('anchored',new)]:
            folder = output/case/label
            status = run_trip_plan(spec, folder, env, facts, saved_snapshot=snapshot, saved_layouts=layouts)
            row[label] = outcome(folder, status)
        save(output/case/'PAIR.json', row)
        print(case, dict((a,row[a].get('metrics',{}).get('city_transit_minutes')) for a in ('baseline','anchored')),flush=True)


def budget(output, env, facts):
    limits = {'human_73':[(4,3000),(4,3300),(4,3600)], 'easy_24':[(1,1600),(2,1600),(2,2000),(2,2400)]}
    summary = []
    for case, attempts in limits.items():
        original = read(P031/case/'request.json')
        for days, cap in attempts:
            spec = deepcopy(original)
            for item in spec['items']:
                if item['field'] == 'days':
                    item['value'] = days
                if item['field'] == 'budget':
                    assert item['unit']=='CNY/group'
                    item['value'] = cap
            snapshot = facts.trip_snapshot(spec)
            inputs, unsupported = constructor_input(spec, snapshot=snapshot)
            assert not unsupported
            prepare_snapshot_routes(inputs,env,facts)
            traveler = Constructor(inputs,env,6,1)
            outbound = traveler.tickets()
            inbound = traveler.tickets(True)
            fixed = {'outbound_min_tested':round(min((r['Cost'] for r in outbound),default=0)*traveler.people,2),
                     'return_min_tested':round(min((r['Cost'] for r in inbound),default=0)*traveler.people,2),
                     'lodging_min_tested':round(min((r['price']*r['_rooms'] for r in traveler.hotels),default=0)*(days-1),2)}
            folder = output/case/f'{days}days_{cap}'
            first = run_trip_plan(spec,folder,env,facts,saved_snapshot=snapshot,recovery_retry=True)
            row = {'case':case, 'days':days,'cap':cap,'fixed_components':fixed,**outcome(folder,first)}
            summary.append(row)
            save(output/'SUMMARY.json',summary)
            print(case,days,cap,row['status'],row.get('metrics',{}).get('total_cost_cny'),flush=True)
            if first['state']=='complete':
                break


def recovery(output, env, facts):
    for case in ('human_73','easy_24'):
        spec = read(P031/case/'request.json')
        folder = output/case
        run_trip_with_recovery(spec, folder, env, facts, try_budget_alternatives=True)
        result = read(folder/'RECOVERY.json')
        affected = next((a for a in result['attempts'] if a.get('cost_above_original_cny')), None)
        if affected:
            assert read(folder/'request.json') == spec
            print(case, affected['view']['candidates'][0]['metrics'], affected['cost_above_original_cny'], flush=True)
        else:
            print(case, result['attempts'], flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite',choices=('quality','budget','recovery'))
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    out=fresh(args.out)
    env=environment(DATA)
    facts=WorldFacts.configured(DATA)
    {'quality': quality, 'budget': budget, 'recovery': recovery}[args.suite](out,env,facts)


if __name__=='__main__':
    main()
