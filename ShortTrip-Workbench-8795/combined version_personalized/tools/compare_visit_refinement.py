"""Replay quality search against exactly the same saved recommended route and constraints."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.plan_assessment import evaluate_archive
from chinatravel.personalization.planning_compat import constructor_input, prepare_snapshot_routes
from chinatravel.personalization.visit_refinement import refine_visits
from chinatravel.personalization.world_facts import WorldFacts


def summary(assessment):
    candidate = next(c for c in assessment['candidates'] if c['id'] == assessment['selection']['recommended_id'])
    q = candidate['quality_vector']
    return {'id': candidate['id'], 'passed': candidate['validation_report']['passed'],
            **{k: q[k] for k in ('visit_minutes','city_transit_minutes','access_walk_minutes','total_cost_cny')},
            'visits': [d['attractions'] for d in q['days']]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('folders', type=Path, nargs='+')
    args = parser.parse_args()
    target = fresh(args.out)
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    results = []
    for base in args.folders:
        for folder in ([base] if (base/'request.json').is_file() else sorted(p for p in base.iterdir() if p.is_dir())):
            row = {'source': str(folder)}
            results.append(row)
            if not (folder/'PLAN_VIEW.json').exists():
                row['state'] = 'no_original_delivery'
                continue
            spec, snap = read(folder/'request.json'), read(folder/'WORLD_FACTS.json')
            req, _ = constructor_input(spec, snapshot=snap)
            prepare_snapshot_routes(req, env, facts)
            archive, old = read(folder/'CANDIDATE_ARCHIVE.json'), read(folder/'PLAN_ASSESSMENT.json')
            updated, assessment, log = refine_visits(spec, snap, archive, old, env, facts, 30)
            row.update(before=summary(old), after=summary(assessment), **log)
            if log['state'] == 'verified_variant':
                replay = evaluate_archive(spec, snap, updated, env, facts)
                assert summary(replay) == row['after']
                assert replay['candidates'][-1]['validation_report']['passed']
                row['variant'] = updated['archive'][-1]['id']
            save(target/'SUMMARY.json', results)
            print(folder.name, row['state'], row['before']['visits'], row['after']['visits'], flush=True)
    save(target/'SUMMARY.json', results)
