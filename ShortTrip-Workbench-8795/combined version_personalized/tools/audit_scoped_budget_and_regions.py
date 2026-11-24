"""Compare scope repair and regional layouts on P034 inputs and model replies."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.trip_sketch import propose_trip_sketches
from chinatravel.personalization.tripspec import ModelReply, normalize_saved_spec
from chinatravel.personalization.world_facts import WorldFacts


EXP = ROOT/'result/experiments'
OLD = EXP/'P034_delivery_and_quality'
NEW = EXP/'P035_scoped_budget_and_regions'


def init_world():
    global env, facts
    env, facts = environment(DATA), WorldFacts.configured(DATA)


def read_model_layouts(source):
    path = source/'TRIP_SKETCHES.json'
    if not path.is_file():
        return ModelReply()
    model = read(path)['model']
    return ModelReply(draft=model.get('draft'), error=model.get('error'), model=model.get('name'))


def invoke(spec, snapshot, layouts, output):
    status = run_trip_plan(spec, output, env, facts, saved_snapshot=snapshot, saved_layouts=layouts)
    row = {'state': status['state'], 'reason': status.get('reason_code'),
           'seconds': status['elapsed_seconds'], 'first_seconds': status.get('first_visible_seconds')}
    path = output/'PLAN_VIEW.json'
    if path.is_file():
        view = read(path)
        assessed = read(output/'PLAN_ASSESSMENT.json')
        checks = {c['id']: c for c in assessed['candidates']}
        shown = view['candidates'][0]
        selected = checks[shown['id']]
        assert selected['validation_report']['passed']
        assert all(r['status'] == 'passed' for r in selected['validation_report']['hard_requirements'])
        row.update(id=shown['id'], source=shown['source'], metrics=shown['metrics'], goals=shown['goals'],
                   budget_checks=shown.get('budget_checks', []), verified=view['verified_count'],
                   regional_candidates=[{'id': r['id'], 'passed': r['validation_report']['passed'],
                                         'quality': r.get('quality_vector'), 'goals': r.get('goals')}
                                        for r in checks.values() if r['source'] == 'nearby_regions'])
    return row


def pair(task):
    index, case, source, output = task
    saved = read(source/'request.json')
    spec = normalize_saved_spec(saved)
    original_budget = [i for i in saved['items'] if i['field'] in ('budget', 'budget_target')]
    budget = iter(original_budget)
    control = {**spec, 'items': [next(budget) if i['field'] in ('budget', 'budget_target') else i
                                for i in spec['items']]}
    reply = read_model_layouts(source)

    def prepare(s):
        if not s['ready']:
            return None, None
        snapshot = facts.trip_snapshot(s)
        sketches = propose_trip_sketches(snapshot, use_model=reply.draft is not None,
                                          provider=lambda *args, **kwargs: reply)
        return snapshot, sketches

    next_snapshot, next_layouts = prepare(spec)
    old_snapshot, old_layouts = prepare(control) if spec != control else (next_snapshot, next_layouts)
    if old_layouts:
        old_layouts = {**old_layouts, 'sketches': [s for s in old_layouts['sketches'] if s['source'] != 'nearby_regions']}
    variants = {'before': (control, old_snapshot, old_layouts), 'after': (spec, next_snapshot, next_layouts)}
    same_inputs = variants['before'] == variants['after']
    evidence = {'case': case, 'budget_updates': spec != control,
                'regional_direction': bool(next_layouts and any(
                    s['source'] == 'nearby_regions' for s in next_layouts['sketches'])),
                'cached_model_reply': reply.draft is not None, 'common_input': same_inputs}
    folder = output/case
    if same_inputs:
        row = invoke(*variants['after'], folder/'common')
        evidence.update(before=row, after=row)
    else:
        order = ('before', 'after') if index % 2 == 0 else ('after', 'before')
        for label in order:
            evidence[label] = invoke(*variants[label], folder/label)
    save(folder/'PAIR.json', evidence)
    print(case, evidence['before']['state'], '->', evidence['after']['state'],
          evidence['before'].get('metrics', {}).get('city_transit_minutes'),
          evidence['after'].get('metrics', {}).get('city_transit_minutes'), flush=True)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--targeted', action='store_true')
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    output = fresh(args.out)
    sources = [(r['case'], OLD/'web_live_01'/r['case']) for r in read(OLD/'FROZEN30.json')['cases']]
    if args.targeted:
        sources = [x for x in sources if x[0] in ('human_104', 'human_141')]
        sources.append(('easy_114', OLD/'quality_02/easy_114/anchored'))
    tasks = [(index, case, source, output) for index, (case, source) in enumerate(sources)]
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_world) as workers:
        rows = list(workers.map(pair, tasks))
    save(output/'SUMMARY.json', rows)


if __name__ == '__main__':
    main()
