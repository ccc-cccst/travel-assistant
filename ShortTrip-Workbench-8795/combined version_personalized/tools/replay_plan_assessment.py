"""Evaluate saved candidates and mutate one field at a time for negative controls."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chinatravel.personalization.cli import DATA, ROOT, environment, fresh, read, save
from chinatravel.personalization.plan_assessment import evaluate_archive
from chinatravel.personalization.world_facts import WorldFacts, clock_minutes


P010 = ROOT / 'result/experiments/P010_trip_world_snapshot/run_02'
P012 = ROOT / 'result/experiments/P012_sketch_planning'
CASES = ('human_69', 'medium_140', 'four_day_appointment')


def probes(case, archive, env):
    source = archive['archive'][0]
    controls = []

    def add(name, mutate, issue):
        bad = deepcopy(source)
        bad.update(id=name, source='negative_control', validation={'passed': True}, quality={'total_cost_cny': 0})
        mutate(bad['itinerary']['itinerary'])
        controls.append((bad, issue))

    if case == 'human_69':
        add('wrong_go_service', lambda days: days[0]['activities'][0].update(TrainID='P013-NOT-IN-SNAPSHOT'),
            'ticket_evidence')
        add('wrong_return_service', lambda days: days[-1]['activities'][-1].update(TrainID='P013-NOT-IN-SNAPSHOT'),
            'ticket_evidence')
        add('forged_beds', lambda days: days[0]['activities'][-1].update(room_type=1), 'hotel_room_evidence')

        def change_cost(days):
            ticket = days[0]['activities'][0]
            ticket['cost'] += 1

        add('forged_ticket_cost', change_cost, 'activity_cost')
    elif case == 'medium_140':
        records = env.accommodations.data['成都'].to_dict('records')
        plain = next(r for r in records if r.get('numbed') == 1 and r.get('featurehoteltype')
                     and '管家服务' not in str(r['featurehoteltype']) and 0 < float(r['price']) < 300)

        def replace_hotel(days):
            hotel = days[0]['activities'][-1]
            hotel.update(position=plain['name'], price=float(plain['price']),
                         cost=float(plain['price']) * hotel['rooms'])

        add('hotel_without_required_facility', replace_hotel, 'hotel_feature')
    else:
        def shift_appointment(days):
            appointment = next(a for a in days[2]['activities'] if a.get('position') == '上海博物馆')
            for key in ('start_time', 'end_time'):
                minute = clock_minutes(appointment[key]) + 5
                appointment[key] = f'{minute // 60:02d}:{minute % 60:02d}'

        add('moved_appointment', shift_appointment, 'appointment_time')
    return controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--case', action='append', choices=CASES)
    args = parser.parse_args()
    out = fresh(args.out)
    env = environment(DATA)
    facts = WorldFacts.configured(DATA)
    summary = []
    for case in dict.fromkeys(args.case or CASES):
        case_input = P010 / case
        snapshot, spec = read(case_input / 'WORLD_FACTS.json'), read(case_input / 'request.json')
        path = P012 / ('run_03_cost_options' if case == 'four_day_appointment' else 'run_02') / case / 'CANDIDATE_ARCHIVE.json'
        original = read(path)
        controls = probes(case, original, env)
        archive = {**original, 'archive': [*original['archive'], *(p for p, _ in controls)]}
        first = deepcopy(archive['archive'][0])
        first.update(quality={'total_cost_cny': 0}, validation={'passed': False}, places=[])
        archive['archive'][0] = first
        result = evaluate_archive(spec, snapshot, archive, env, facts)
        folder = out / case
        save(folder / 'PLAN_ASSESSMENT.json', result)
        candidates = {a['id']: a for a in result['candidates']}
        checks = {}
        for probe, expected in controls:
            report = candidates[probe['id']]['validation_report']
            codes = [i['code'] for i in report['issues']]
            checks[probe['id']] = {'expected': expected, 'codes': codes,
                                   'rejected_as_expected': expected in codes and not report['passed']}
        save(folder / 'NEGATIVE_CONTROLS.json', checks)
        assert all(p['rejected_as_expected'] for p in checks.values()), checks
        assert result['verified_count'] == len(original['archive']), result['verified_count']
        assert candidates[first['id']]['quality_vector']['total_cost_cny'] == original['archive'][0]['quality']['total_cost_cny']
        pick = result['selection']['recommended_id']
        assert pick in {a['id'] for a in original['archive']}
        row = {'case': case, 'archive': path.relative_to(ROOT).as_posix(),
               'verified': result['verified_count'], 'negative_controls': len(checks),
               'recommended_id': pick, 'alternatives': result['selection']['alternatives'],
               'metrics': {k: candidates[pick]['quality_vector'][k]
                           for k in ('total_cost_cny', 'city_transit_minutes', 'access_walk_minutes', 'visit_minutes')},
               'preparation_seconds': result['preparation_seconds'], 'assessment_seconds': result['assessment_seconds'],
               'model_calls': result['model_calls']}
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    save(out / 'SUMMARY.json', {'cases': summary})


if __name__ == '__main__':
    main()
