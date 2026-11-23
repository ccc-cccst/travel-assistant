"""Capture DeepSeek sketches on synthetic frozen trips and replay the same inputs."""
import argparse
from pathlib import Path

from check_visit_day import FROZEN, fingerprint, prepare_inputs
from check_terminal_access import DATA, WorldFacts, environment, fresh, read, run_case, save
from chinatravel.personalization.plan_assessment import select_candidates
from chinatravel.personalization.trip_sketch import propose_trip_sketches


CASES = ('bj_palace', 'sh_observatory', 'cd_dujiangyan', 'ordinary_nanjing', 'locked_shanghai')


def trace_model_sketches(sketches, archive, selection):
    visible = {selection['recommended_id'], *(option['id'] for option in selection['alternatives'])}
    traces = []
    for sketch in (sketch for sketch in sketches if sketch['source'] == 'model'):
        entries = [entry for entry in archive if entry['sketch_id'] == sketch['id']]
        trace = {'sketch_id': sketch['id'], 'candidates': []}
        for entry in entries:
            placed = {place['id']: place['day'] for place in entry['places']}
            trace['candidates'].append({
                'id': entry['id'],
                'visible': entry['id'] in visible,
                'core_places': [
                    {'id': place, 'suggested_day': day['day'], 'scheduled_day': placed.get(place)}
                    for day in sketch['days'] for place in day['core_ids']
                ],
            })
        traces.append(trace)
    return traces


def compare_combined_archive(program, model):
    original = program['candidates']
    initial = program['selection']['recommended_id']
    known = {candidate['id'] for candidate in original}
    extra = [candidate for candidate in model['candidates'] if candidate['source'] == 'model'
             and candidate['validation_report']['passed'] and candidate['id'] not in known]
    selection = select_candidates(original + extra, initial_recommended_id=initial)
    visible = {selection['recommended_id'], *(option['id'] for option in selection['alternatives'])}
    return {'original_recommended_id': initial, 'actual_model_recommended_id': model['selection']['recommended_id'],
            'new_model_ids': [candidate['id'] for candidate in extra],
            'visible_new_model_ids': [candidate['id'] for candidate in extra if candidate['id'] in visible],
            'merged_selection': selection}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--late-replay', action='store_true')
    parser.add_argument('--saved-from', type=Path)
    args = parser.parse_args()
    manifest = {item['id']: item for item in read(FROZEN / 'MANIFEST.json')}
    for case in CASES:
        if fingerprint(FROZEN / case) != manifest[case]['checksums']:
            raise ValueError(f'Frozen input changed: {case}')
    if not (args.replay or args.late_replay):
        out = fresh(args.out)
        summary = {}
        for case in CASES:
            snapshot = read(FROZEN / case / 'WORLD_FACTS.json')
            layouts = propose_trip_sketches(snapshot, use_model=True, timeout=12)
            save(out / case / 'TRIP_SKETCHES.json', layouts)
            model = layouts['model']
            summary[case] = {'attempts': model['attempts'], 'error': model['error'],
                             'elapsed_seconds': model['elapsed_seconds'], 'usage': model['usage'],
                             'model_layouts': sum(sketch['source'] == 'model' for sketch in layouts['sketches']),
                             'rejected': model['rejected']}
            save(out / 'CAPTURE.json', summary)
            print(case, summary[case], flush=True)
        return

    out = fresh(args.out / 'paired')
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    results = {}
    comparisons = {}
    for index, case in enumerate(CASES):
        source = FROZEN / case
        layouts = read((args.saved_from or args.out) / case / 'TRIP_SKETCHES.json')
        initialization = prepare_inputs(source, env, facts)
        rows = {}
        model_arm = 'model_late' if args.late_replay else 'model'
        arms = (('program', None), (model_arm, layouts))
        for label, proposal in arms if index % 2 == 0 else reversed(arms):
            folder = out / case / label
            row = run_case(source, folder, env, facts, None, saved_layouts=proposal,
                           model_after_search=label == 'model_late')
            if row['state'] == 'complete':
                selection = read(folder / 'PLAN_ASSESSMENT.json')['selection']
                archive = read(folder / 'CANDIDATE_ARCHIVE.json')['archive']
                row['visible_ids'] = [selection['recommended_id'], *(option['id'] for option in selection['alternatives'])]
                if proposal:
                    row['model_traces'] = trace_model_sketches(proposal['sketches'], archive, selection)
            rows[label] = row
            print(case, label, row['state'], row.get('verified'), flush=True)
        results[case] = {'initialization_seconds': initialization, **rows}
        save(out / 'SUMMARY.json', results)
        if not args.late_replay and all(row['state'] == 'complete' for row in rows.values()):
            program = read(out / case / 'program/PLAN_ASSESSMENT.json')
            model = read(out / case / 'model/PLAN_ASSESSMENT.json')
            comparisons[case] = compare_combined_archive(program, model)
            save(out / 'RETAIN_BASELINE_REPLAY.json', comparisons)


if __name__ == '__main__':
    main()
