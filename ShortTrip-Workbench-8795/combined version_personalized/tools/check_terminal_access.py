"""Compare first-stop access ordering on frozen V20 requests."""
import argparse
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.place_preferences import update_place_preference
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.trip_world import snapshot_for_trip
from chinatravel.personalization.tripspec import normalize_saved_spec
from chinatravel.personalization.world_facts import WorldFacts


HANGZHOU = ROOT / 'result/experiments/P050_first_plan_places'
CASES = {
    'hangzhou_selected': HANGZHOU / 'paired_01_selected',
    'hangzhou_ordinary': HANGZHOU / 'paired_01_baseline',
    'shanghai_appointment': ROOT / 'result/experiments/P039_full_pace/constraints_03/fixed_appointment/default',
}
GENERALIZATION = ('easy_162', 'easy_174', 'human_99', 'medium_19')


def freeze_map_cases(out, facts):
    sources = ROOT / 'result/experiments/P045_workbench_acceptance/live_01'
    for case in GENERALIZATION:
        spec = normalize_saved_spec(read(sources / case / 'SPEC.json'))
        initial = snapshot_for_trip(facts, spec)
        places = [place for place in initial['candidates']['attraction']
                  if place['planning_catalog'] and place['routable_in_snapshot']]
        for index, choice in ((0, 'want'), (3, 'must'), (4, 'skip')):
            spec = update_place_preference(spec, facts, places[index]['id'], choice)
        snapshot = snapshot_for_trip(facts, spec)
        frozen = out / 'frozen' / case
        save(frozen / 'request.json', spec)
        save(frozen / 'WORLD_FACTS.json', snapshot)
        save(frozen / 'TRIP_SKETCHES.json', propose_trip_sketches(snapshot))
        yield case, frozen


def run_case(source, destination, env, facts, targeted_trial, *, saved_layouts=None, model_after_search=False):
    state = run_trip_plan(
        read(source / 'request.json'), destination, env, facts,
        saved_snapshot=read(source / 'WORLD_FACTS.json'),
        saved_layouts=read(source / 'TRIP_SKETCHES.json') if saved_layouts is None else saved_layouts,
        seconds=14, candidates=6, search_deadline=perf_counter() + 35,
        targeted_trial=targeted_trial, model_after_search=model_after_search,
    )
    row = {'state': state['state'], 'elapsed_seconds': state['elapsed_seconds']}
    if state['state'] != 'complete':
        return row
    archive = read(destination / 'CANDIDATE_ARCHIVE.json')
    assessment = read(destination / 'PLAN_ASSESSMENT.json')
    first = read(destination / 'FIRST_VIEW.json')
    best_id = assessment['selection']['recommended_id']
    candidates = []
    for candidate in assessment['candidates']:
        assert candidate['validation_report']['passed']
        assert all(check['status'] == 'passed'
                   for check in candidate['validation_report']['hard_requirements'])
        candidates.append({'id': candidate['id'], 'source': candidate['source'],
                           **candidate['quality_vector'],
                           'matched_goals': [goal['value'] for goal in candidate['goals'] if goal['status'] == 'matched']})
    return {**row, 'verified': len(candidates), 'first_seconds': first['elapsed_seconds'],
            'first_id': first['candidate']['id'], 'recommended_id': best_id, 'route_calls': archive['route_calls'],
            'search_seconds': archive['search_seconds'], 'stop_reason': archive['stop_reason'],
            'comparison_count': len(assessment['selection']['alternatives']),
            'model_alternative': archive.get('model_alternative'),
            'targeted_trials': [d for d in archive['diagnostics']
                                if d.get('arrival_access_anchor') or d.get('earliest_required_visit_days')],
            'candidates': candidates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    parser.add_argument('--trial', choices=('arrival_access', 'visit_day', 'visit_day_full'), default='arrival_access')
    parser.add_argument('--only', choices=('baseline', 'trial'), help='Run one arm in a fresh process')
    parser.add_argument('--additional-cities', action='store_true')
    args = parser.parse_args()
    out = fresh(args.out)
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    results = {}
    sources = {name: CASES[name] for name in args.cases}
    if args.additional_cities:
        sources.update(freeze_map_cases(out, facts))
    arms = {'baseline': ('baseline',), 'trial': (args.trial,)}.get(args.only, ('baseline', args.trial))
    for name, source in sources.items():
        results[name] = {}
        for arm in arms:
            row = run_case(source, out / name / arm, env, facts, None if arm == 'baseline' else arm)
            results[name][arm] = row
            save(out / 'SUMMARY.json', results)
            selected = next((c for c in row.get('candidates', []) if c['id'] == row['recommended_id']), {})
            print(name, arm, row['state'], row.get('verified'), selected.get('city_transit_minutes'),
                  selected.get('visit_minutes'), flush=True)


if __name__ == '__main__':
    main()
