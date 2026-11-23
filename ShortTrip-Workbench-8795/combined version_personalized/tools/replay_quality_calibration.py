"""Compare soft-goal evaluations on the same P016 candidate archives."""

import argparse
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay_integrated_planning import RESULTS, select_cases
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.plan_assessment import evaluate_archive
from chinatravel.personalization.world_facts import WorldFacts


SOURCE = RESULTS / 'P016_meal_refinement/paired_02'


def compare(old, new):
    old_by_id = {c['id']: c for c in old['candidates']}
    assert old_by_id.keys() == {c['id'] for c in new['candidates']}
    shifts = []
    for current in new['candidates']:
        prior = old_by_id[current['id']]
        assert prior['validation_report'] == current['validation_report'], current['id']
        assert prior.get('quality_vector') == current.get('quality_vector'), current['id']
        old_goals = {g['requirement_id']: g for g in prior.get('goals', ())}
        for goal in current.get('goals', ()):
            before = old_goals[goal['requirement_id']]
            if goal['status'] != before['status'] or goal['matched_places'] != before['matched_places']:
                shifts.append({'candidate_id': current['id'], 'field': goal['field'], 'value': goal['value'],
                               'before': before['status'], 'after': goal['status'],
                               'tag_evidence': goal.get('resolved_tags', []), 'places': goal['matched_places']})
    return shifts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = fresh(args.out)
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    rows = []
    for case in select_cases('frozen30'):
        folder = SOURCE / case / 'refined'
        state = read(folder / 'STATUS.json')['state']
        row = {'id': case, 'recorded_state': state, 'source': str(folder)}
        if state == 'complete':
            old = read(folder / 'PLAN_ASSESSMENT.json')
            new = evaluate_archive(read(folder / 'request.json'), read(folder / 'WORLD_FACTS.json'),
                                   read(folder / 'CANDIDATE_ARCHIVE.json'), env, facts)
            row.update(verified=new['verified_count'], changes=compare(old, new))
            for name, assessment in (('before', old), ('after', new)):
                pick = assessment['selection']['recommended_id']
                recommendation = next(c for c in assessment['candidates'] if c['id'] == pick)
                row[name] = {'id': pick, 'goals': Counter(g['status'] for g in recommendation['goals']),
                             'alternatives': [a['id'] for a in assessment['selection']['alternatives']],
                             'metrics': {k: recommendation['quality_vector'][k] for k in
                                         ('total_cost_cny', 'city_transit_minutes', 'access_walk_minutes', 'visit_minutes')}}
            save(out / case / 'PLAN_ASSESSMENT.json', new)
        rows.append(row)
        print(case, state, 'changed evaluations', len(row.get('changes', [])), flush=True)
    save(out / 'SUMMARY.json', {'case_states': dict(Counter(row['recorded_state'] for row in rows)),
                                'verified_candidates': sum(row.get('verified', 0) for row in rows),
                                'model_calls': 0, 'cases': rows})


if __name__ == '__main__':
    main()
