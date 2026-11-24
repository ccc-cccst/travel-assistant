"""Replay stored submissions through the same first-plan entry as the web worker."""
import argparse
from collections import Counter
from hashlib import sha256
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.tripspec import resolve_conflicts
from chinatravel.personalization.world_facts import WorldFacts


P029 = ROOT/'result/experiments/P029_generalization'
PAIRS = P029/'paired_01'


def cases(suite):
    if suite == 'regression':
        ids = read(PAIRS/'MANIFEST.json')['groups']['independent']
        return [(case, PAIRS/'independent'/case/'on') for case in ids]
    return [(case, PAIRS/group/case/'on') for group, ids in (
        ('independent', ('easy_24','medium_122','human_9')),
        ('confirmed', ('human_73','human1000_541','human1000_666'))
    ) for case in ids] + [('human_60', P029/'related_01/human_60/on'),
                           ('human_9_confirmed', PAIRS/'independent/human_9/on')]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--out', type=Path, required=True)
    cli.add_argument('--suite', choices=('blocked','regression'), required=True)
    args = cli.parse_args()
    out = fresh(args.out)
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    rows = []
    for case, source in cases(args.suite):
        original = source/'request.json'
        spec = read(original)
        if case == 'human_9_confirmed':
            question, = spec['issues']
            option, = [idx for idx,item in enumerate(question['choices']) if item['field']=='days' and item['value']==7]
            spec = resolve_conflicts(spec, {question['id']: option})
        dest = out/case
        state = run_trip_plan(spec, dest, env, facts, seconds=24, candidates=6)
        row = {'case':case, 'source':str(source.relative_to(ROOT)), 'request_sha256':sha256(original.read_bytes()).hexdigest(),
               'prior_state':read(source/'STATUS.json')['state'], 'state':state['state'],
               'reason':state.get('reason_code') or state.get('error_code'), 'verified':0,
               'stage':state['stage'], 'elapsed_seconds':state['elapsed_seconds']}
        if state['state']=='complete':
            a = read(dest/'PLAN_ASSESSMENT.json')
            eligible = [c for c in a['candidates'] if c['validation_report']['passed']]
            assert len(eligible)==state['candidate_count']==a['verified_count']
            assert all(h['status']=='passed' for c in eligible for h in c['validation_report']['hard_requirements'])
            view = read(dest/'PLAN_VIEW.json')
            selected, = [c for c in eligible if c['id']==view['recommended_id']]
            assert view['candidates'][0]['id'] == selected['id'] and view['candidates'][0]['days']
            assert read(dest/'FIRST_VIEW.json')['candidate']['id'] in {c['id'] for c in eligible}
            row.update(verified=len(eligible), pick=selected['id'], metrics=view['candidates'][0]['metrics'])
            if (source/'PLAN_VIEW.json').exists():
                row['same_pick']=read(source/'PLAN_VIEW.json')['recommended_id']==selected['id']
                previous = read(source/'PLAN_ASSESSMENT.json')
                row['same_verified_ids'] = ({c['id'] for c in eligible} ==
                    {c['id'] for c in previous['candidates'] if c['validation_report']['passed']})
        rows.append(row)
        save(out/'SUMMARY.json', {'suite':args.suite, 'model_calls':0, 'seconds':24, 'cap':6,
                                  'states':dict(Counter(r['state'] for r in rows)), 'rows':rows})
        print(case,state['state'],row['verified'],flush=True)


if __name__=='__main__':
    main()
