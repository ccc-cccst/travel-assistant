"""Rebuild the frozen Beijing itinerary around another hotel."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chinatravel.personalization.cli import ROOT, DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import _card
from chinatravel.personalization.plan_assessment import evaluate_archive
from chinatravel.personalization.planning_compat import constructor_input, prepare_snapshot_routes
from chinatravel.personalization.sketch_planning import _digest, _rebuild_day
from chinatravel.personalization.world_facts import WorldFacts
from chinatravel.planning.engine import Constructor, SearchBudgetExpired, clock, minutes, total_cost


from chinatravel.personalization.hotel_change import rebuild_with_meal_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = fresh(args.out)
    source = ROOT/'result/experiments/P034_delivery_and_quality/web_live_01/human_141'
    spec, baseline = read(source/'request.json'), read(source/'PLAN_VIEW.json')
    selected = next(r for r in read(source/'CANDIDATE_ARCHIVE.json')['archive']
                    if r['id'] == baseline['recommended_id'])
    original = selected['itinerary']
    old_hotel = next(a['position'] for d in original['itinerary'] for a in d['activities']
                     if a['type'] == 'accommodation')
    save(out/'ORIGINAL_VIEW.json',baseline['candidates'][0])
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    snapshot = facts.trip_snapshot(spec)
    request, unsupported = constructor_input(spec, snapshot=snapshot)
    assert not unsupported
    prepare_snapshot_routes(request, env, facts)
    traveler = Constructor(request, env, 26, 1)
    anchors = [a['position'] for d in original['itinerary'] for a in d['activities']
               if a['type'] in ('attraction', 'lunch', 'dinner', 'breakfast')]
    eligible_hotels = [h for h in traveler.hotels if h['name'] != old_hotel]
    nearby = sorted(eligible_hotels, key=lambda h: sum(traveler.distance(h['name'], a) for a in anchors))
    cheap = sorted(eligible_hotels, key=lambda h: h['price']*h['_rooms'])
    hotels = {h['name']:h for near, low in zip(nearby[:24],cheap[:24]) for h in (near,low)}
    archive = []
    tried = 0
    failed_days = Counter()
    for hotel in hotels.values():
        tried += 1
        plan = deepcopy(original)
        for day in plan['itinerary']:
            for stop in day['activities']:
                if stop['type'] == 'accommodation':
                    stop.update(position=hotel['name'], price=hotel['price'], cost=hotel['price']*stop['rooms'])
                    if hotel['_bed_count'] is not None:
                        stop['room_type'] = hotel['_bed_count']
                    else:
                        stop.pop('room_type', None)
        built = []
        try:
            for day in plan['itinerary']:
                locks = {(a['place'],a['start_time']) for a in request['appointments'] if a['day']==day['day']}
                rebuilt = rebuild_with_meal_window(traveler, day, hotel['name'], locks, facts)
                if not rebuilt:
                    failed_days[day['day']] += 1
                    break
                built.append(rebuilt)
        except SearchBudgetExpired:
            break
        if len(built) == len(plan['itinerary']):
            plan['itinerary'] = built
            archive.append({'id':_digest(plan),'source':'hotel_swap_probe','itinerary':plan})
    assessment = evaluate_archive(spec, snapshot, {'schema_version':'sketch_trial_v1',
                                                    'snapshot_id':snapshot['snapshot_id'],
                                                    'archive':archive}, env, facts)
    save(out/'ASSESSMENT.json',assessment)
    eligible = [r for r in assessment['candidates'] if r['validation_report']['passed']]
    metrics = {'tried':tried,'verified':len(eligible),'before_hotel':old_hotel,'failed_days':dict(failed_days)}
    if eligible:
        best = min(eligible, key=lambda r: (r['quality_vector']['city_transit_minutes'],
                                            r['quality_vector']['total_cost_cny']))
        raw = next(r for r in archive if r['id']==best['id'])
        card = _card(raw,best,spec)
        save(out/'ADJUSTED_VIEW.json',card)
        metrics.update(after_hotel=next(a['name'] for d in card['days'] for a in d['activities']
                                       if a['kind']=='accommodation'), after=card['metrics'])
    save(out/'SUMMARY.json',metrics)
    print(metrics,flush=True)


if __name__=='__main__':
    main()
