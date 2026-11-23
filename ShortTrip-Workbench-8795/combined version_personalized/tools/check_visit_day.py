"""Freeze and replay short trips through the existing V20 planning entrypoint."""
import argparse
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from check_terminal_access import CASES, DATA, ROOT, WorldFacts, environment, fresh, read, run_case, save
from chinatravel.personalization.place_preferences import update_place_preference
from chinatravel.personalization.planning_compat import constructor_input, prepare_snapshot_routes
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.trip_world import snapshot_for_trip
from chinatravel.personalization.tripspec import interpret


EXPERIMENT = ROOT / 'result/experiments/P052_visit_day'
FROZEN = EXPERIMENT / 'frozen'
HUMAN_99 = ROOT / 'result/experiments/P051_terminal_access/paired_05/frozen/human_99'
FILES = ('request.json', 'WORLD_FACTS.json', 'TRIP_SKETCHES.json')


def fingerprint(source):
    return {key: sha256((source / key).read_bytes()).hexdigest() for key in FILES}


def freeze(facts):
    inputs = []
    cases = read(EXPERIMENT / 'CASES.json')
    for case in cases:
        form = {'start_city': case['from'], 'target_city': case['to'],
                'days': case['days'], 'people_number': case['people'], 'intercity_modes': ['train']}
        if 'budget' in case:
            form['budget'] = case['budget']
        if 'appointment' in case:
            form['appointments'] = [{'name': case['appointment'], 'day': 2,
                                     'start_time': '15:00', 'duration_minutes': 60}]
        spec = interpret({'form': form}, facts.sandbox.cities)
        if not spec['ready']:
            raise ValueError(f"{case['id']}: {spec['issues']}")
        for choice in ('must', 'want'):
            if choice not in case:
                continue
            match = facts.place(case['to'], case[choice], 'attraction')
            if match['status'] != 'found':
                raise ValueError(f"{case['id']}: {case[choice]} {match['status']}")
            place, = match['records']
            spec = update_place_preference(spec, facts, place['id'], choice)
        snapshot = snapshot_for_trip(facts, spec)
        layouts = propose_trip_sketches(snapshot)
        inputs.append((case, spec, snapshot, layouts))

    out = fresh(FROZEN)
    manifest = []
    for case, spec, snapshot, layouts in inputs:
        source = out / case['id']
        for name, value in zip(FILES, (spec, snapshot, layouts)):
            save(source / name, value)
        manifest.append({**case, 'checksums': fingerprint(source)})
    save(out / 'MANIFEST.json', manifest)


def replay(source, destination, env, facts, trial):
    row = run_case(source, destination, env, facts, trial)
    status = read(destination / 'STATUS.json')
    row['reason'] = status.get('reason_code') or status.get('error_code')
    if row['state'] == 'complete':
        archive = read(destination / 'CANDIDATE_ARCHIVE.json')
        selection = read(destination / 'PLAN_ASSESSMENT.json')['selection']
        row['visible_ids'] = [selection['recommended_id'], *(alt['id'] for alt in selection['alternatives'])]
        row['frontier_ids'] = selection['frontier_ids']
        row['late_trial'] = archive.get('targeted_trial')
        row['trial_candidate_ids'] = [
            item['id'] for item in archive['archive']
            if item['sketch_id'] in {trial_log['sketch_id'] for trial_log in row['targeted_trials']}
        ]
    return row


def prepare_inputs(source, env, facts):
    start = perf_counter()
    spec = read(source / 'request.json')
    snapshot = read(source / 'WORLD_FACTS.json')
    request, _ = constructor_input(spec, snapshot=snapshot)
    prepare_snapshot_routes(request, env, facts)
    return perf_counter() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--diagnostics', action='store_true', help='Replay the two P051 development cases')
    parser.add_argument('--warm-inputs', action='store_true', help='Initialize V20 before either measured arm')
    parser.add_argument('--trial', action='append',
                        choices=('visit_day_full', 'visit_day_full_budget', 'visit_day_after_search'))
    args = parser.parse_args()
    if args.freeze == (args.out is not None):
        parser.error('choose --freeze or --out')
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    if args.freeze:
        freeze(facts)
        return

    manifest = read(FROZEN / 'MANIFEST.json')
    sources = [(case['id'], FROZEN / case['id']) for case in manifest]
    for case, (_, source) in zip(manifest, sources):
        if fingerprint(source) != case['checksums']:
            raise ValueError(f"Frozen input changed: {case['id']}")
    if args.diagnostics:
        sources = [('human_99', HUMAN_99), ('hangzhou_selected', CASES['hangzhou_selected'])]
    out = fresh(EXPERIMENT / args.out)
    results = {}
    for index, (name, source) in enumerate(sources):
        trials = args.trial or ['visit_day_full']
        if len(trials) == 2 and not any(item['field'] == 'budget' for item in read(source / 'request.json')['items']):
            trials = ['visit_day_full']
        arms = [('baseline', None), *((trial, trial) for trial in trials)]
        initialization = prepare_inputs(source, env, facts) if args.warm_inputs else None
        results[name] = {}
        for label, trial in arms if index % 2 == 0 else reversed(arms):
            row = replay(source, out / name / label, env, facts, trial)
            row['input_initialization_seconds'] = initialization
            results[name][label] = row
            save(out / 'SUMMARY.json', results)
            print(name, label, row['state'], row.get('verified'), len(row.get('targeted_trials', [])), flush=True)


if __name__ == '__main__':
    main()
