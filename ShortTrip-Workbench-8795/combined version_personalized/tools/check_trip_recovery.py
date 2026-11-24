"""Replay frozen requests with recovery enabled and verify delivered artifacts."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_first_plan import cases
from chinatravel.personalization.cli import ROOT, DATA, environment, fresh, read, save
from chinatravel.personalization.trip_recovery import run_trip_with_recovery
from chinatravel.personalization.world_facts import WorldFacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--suite', choices=('blocked', 'regression'), required=True)
    args = parser.parse_args()
    out = fresh(args.out)
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    rows = []
    for case, source in cases(args.suite):
        if case == 'human_9_confirmed':
            continue
        dest = out/case
        original = read(source/'request.json')
        first = run_trip_with_recovery(original, dest, env, facts)
        recovered = read(dest/'RECOVERY.json')
        assert recovered['model_calls'] == 0
        assert read(dest/'request.json')['request_revision'] == original['request_revision']
        summary = {'case': case, 'initial': first['state'], 'initial_seconds': first['elapsed_seconds'],
                   'extra_seconds': recovered.get('elapsed_seconds', 0), 'previews': [], 'draft': None}
        if first['state'] == 'complete':
            summary['outcome'] = 'original'
            comparison = ROOT/'result/experiments/P031_first_plan/regression_02'/case/'PLAN_VIEW.json'
            if comparison.exists():
                summary['same_pick'] = read(dest/'PLAN_VIEW.json')['recommended_id'] == read(comparison)['recommended_id']
        elif recovered['restored']:
            summary['outcome'] = 'restored_original'
        else:
            summary['outcome'] = 'no_plan'
        for attempt in recovered['attempts']:
            if not attempt.get('view') and attempt['id'] != recovered['restored']:
                continue
            archive = read(dest/attempt['id']/'PLAN_ASSESSMENT.json')
            view = read(dest/attempt['id']/'PLAN_VIEW.json')
            checked = [c for c in archive['candidates'] if c['validation_report']['passed']]
            assert len(checked) == view['verified_count']
            assert view['recommended_id'] in {c['id'] for c in checked}
            assert all(h['status'] == 'passed' for c in checked for h in c['validation_report']['hard_requirements'])
            if attempt.get('view'):
                summary['outcome'] = 'conditional'
                summary['previews'].append({'change': attempt['change'], 'verified': len(checked),
                                             'cost_cny': view['candidates'][0]['metrics']['total_cost_cny']})
        if draft := recovered.get('draft'):
            assert summary['outcome'] == 'no_plan' and all(p['source'] for p in draft['stops'])
            summary.update(outcome='city_draft', draft=[p['name'] for p in draft['stops']])
        rows.append(summary)
        save(out/'SUMMARY.json', rows)
        print(case, summary['initial'], summary['outcome'], flush=True)


if __name__ == '__main__':
    main()
