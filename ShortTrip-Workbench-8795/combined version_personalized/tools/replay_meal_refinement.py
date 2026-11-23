"""Replay three saved plans or run paired end-to-end checks on the frozen 30 cases."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay_integrated_planning import RESULTS, select_cases
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.meal_refinement import refine_meals
from chinatravel.personalization.plan_assessment import evaluate_archive
from chinatravel.personalization.world_facts import WorldFacts


SAVED = {'medium_140': 'P015_planning_coverage/frozen30_02/medium_140',
         'medium_104': 'P015_planning_coverage/frozen30_02/medium_104',
         'four_day_appointment': 'P014_integrated_planning/live_appointment'}
METRICS = ('total_cost_cny', 'meal_cost_cny', 'city_transit_minutes', 'access_walk_minutes',
           'visit_minutes', 'peak_daily_city_transit_minutes', 'highest_cost_meal')


def selected(assessment):
    candidate_id = assessment['selection']['recommended_id']
    if candidate_id is None:
        return None
    item = next(c for c in assessment['candidates'] if c['id'] == candidate_id)
    return {'id': candidate_id, 'verified_candidates': assessment['verified_count'],
            'alternatives': len(assessment['selection']['alternatives']),
            'metrics': {key: item['quality_vector'][key] for key in METRICS},
            'goals': [{key: g[key] for key in ('field', 'value', 'status')} for g in item['goals']],
            'hard_checks': [{'field': r['field'], 'status': r['status']}
                            for r in item['validation_report']['hard_requirements']]}


def saved_cases(out, env, facts, seconds):
    for case, folder in SAVED.items():
        path = RESULTS / folder
        spec = read(path / 'request.json')
        snapshot = read(path / 'WORLD_FACTS.json')
        original = read(path / 'CANDIDATE_ARCHIVE.json')
        assessed = evaluate_archive(spec, snapshot, original, env, facts)
        updated, result, evidence = refine_meals(spec, snapshot, original, assessed, env, facts,
                                                 seconds=seconds, max_candidates=6)
        save(out / case / 'CANDIDATE_ARCHIVE.json', updated)
        save(out / case / 'PLAN_ASSESSMENT.json', result)
        yield {'id': case, 'source': folder, 'before': selected(assessed), 'after': selected(result),
               'refinement': evidence}


def paired_cases(out, env, facts, seconds):
    for index, (case, spec) in enumerate(select_cases('frozen30').items()):
        order = ('baseline', 'refined') if index % 2 == 0 else ('refined', 'baseline')
        row = {'id': case, 'order': order}
        for arm in order:
            folder = out / case / arm
            status = run_trip_plan(spec, folder, env, facts, seconds=seconds, improve_meals=arm == 'refined')
            assessment = folder / 'PLAN_ASSESSMENT.json'
            refinement = folder / 'MEAL_REFINEMENT.json'
            row[arm] = {k: status.get(k) for k in ('state', 'first_visible_seconds', 'elapsed_seconds',
                                                 'candidate_count', 'timings', 'model')}
            row[arm]['recommended'] = selected(read(assessment)) if assessment.exists() else None
            if refinement.exists():
                row[arm]['refinement'] = read(refinement)
        yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--paired', action='store_true')
    parser.add_argument('--seconds', type=float, default=24)
    args = parser.parse_args()
    out = fresh(args.out)
    start = perf_counter()
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    summary = {'initialization_seconds': perf_counter() - start, 'cases': []}
    replay = paired_cases if args.paired else saved_cases
    for row in replay(out, env, facts, args.seconds):
        summary['cases'].append(row)
        if args.paired:
            summary['states'] = {arm: dict(Counter(r[arm]['state'] for r in summary['cases']))
                                 for arm in ('baseline', 'refined')}
        save(out / 'SUMMARY.json', summary)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
