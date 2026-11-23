"""Compare program planning, model sketches and a direct itinerary on fixed original cases."""

import argparse
from collections import Counter
from dataclasses import asdict
from functools import partial
from getpass import getpass
from hashlib import sha256
from pathlib import Path
from random import Random
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay_integrated_planning import RESULTS, select_cases
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.plan_assessment import evaluate_archive
from chinatravel.personalization.planning_compat import constructor_input, prepare_snapshot_routes
from chinatravel.personalization.trip_sketch import _appointments, _model_payload, _records
from chinatravel.personalization.tripspec import MODEL, request_deepseek_json
from chinatravel.personalization.world_facts import WorldFacts
from chinatravel.planning.engine import Constructor, SearchBudgetExpired, minutes


ROOT = RESULTS / 'P020_planning_model_comparison'
CASES = ('easy_61', 'easy_208', 'easy_253', 'easy_279',
         'medium_104', 'medium_106', 'medium_140', 'medium_16',
         'human_66', 'human_69', 'human_85', 'human_78')
ARMS = ('program', 'model_sketch', 'direct_model')
SECONDS, TOKENS, MODEL_TIMEOUT, CANDIDATES = 24, 1536, 12, 6
DIRECT_PROMPT = """按相同的旅行事实给出一份可执行的整趟旅行计划，保留所有已知硬要求和固定预约，优先兑现用户偏好。
只用数据提供的 ID，out 和 back 使用对应班次数组的零起始索引。每天从酒店出发，第一天从去程到达站出发。
每个活动恰好是数组 [地点ID, 类型, 开始HH:MM, 结束HH:MM, 到达该点的市内方式]。
类型仅允许 attraction、lunch、dinner、accommodation。住宿每天最多一个，必须放在该日末尾并结束于24:00，返程日没有住宿。
市内方式从 local_modes 选择，返程到站方式单独用 return_mode。预留市内接驳、火车进站60分钟、飞机进场120分钟。
若午餐窗口留有一小时且出发约束允许，午餐11:00至15:00、开始早于14:00。晚餐17:00至21:00、开始早于20:00。两餐各至少60分钟。
每个日对象的 start 是当天从酒店出发的时刻。首日实际从去程到达时刻开始。routes 不由模型编造。
引用 opening_minutes 判断开放区间。票价按人，住宿按晚和房间。只有确实无法提供时可返回空 days。
只输出 JSON 对象示例 {"out":0,"back":1,"days":[{"start":"08:30","stops":[["attraction:example:0","attraction","10:00","11:30","metro"]]}],"return_mode":"metro"}。"""


def frozen_manifest():
    specs = select_cases('frozen30')
    return {
        'cases': [{'id': cid, 'level': cid.split('_')[0],
                   'raw_sha256': sha256(specs[cid]['raw_text'].encode()).hexdigest()} for cid in CASES],
        'arms': list(ARMS), 'candidate_cap': CANDIDATES, 'planner_seconds': SECONDS,
        'model': MODEL, 'output_tokens_cap': TOKENS, 'request_timeout_seconds': MODEL_TIMEOUT,
        'review_seed': 20261120,
    }


def model_facts(snapshot, traveler):
    records = _records(snapshot)
    locks, _ = _appointments(snapshot, records)
    payload = _model_payload(snapshot, records, locks)
    payload['requirements'] = snapshot['requirements']
    payload['origin'] = snapshot['origin']
    payload['local_modes'] = list(traveler.request.get('local_modes', ('metro', 'taxi', 'walk')))
    payload['fixed_appointment_details'] = locks
    payload['place_columns'] += ['opening_minutes', 'price', 'price_unit', 'bed_count']
    for place in payload['places']:
        row = records[place[0]]
        place.extend((row['opening']['windows_minutes'], row['price']['value'], row['price']['unit'],
                      row['benchmark_bed_count']))
    outbound, inbound = traveler.tickets(), traveler.tickets(True)
    payload['ticket_columns'] = ['service', 'mode', 'from', 'to', 'departure', 'arrival', 'fare_per_person']
    for field, rows in (('outbound', outbound), ('return', inbound)):
        payload[field] = [[r.get('TrainID', r.get('FlightID')), r['_kind'], r['From'], r['To'],
                           r['BeginTime'], r['EndTime'], r['Cost']] for r in rows]
    return payload, records, outbound, inbound


def normalize_direct(draft, records, outbound, inbound, traveler):
    """Fill snapshot fields and resolve the routes exactly as requested by the model."""
    plan_days = draft['days']
    if len(plan_days) != traveler.days:
        raise ValueError('days_count')

    def choice(rows, index):
        if type(index) is not int or not 0 <= index < len(rows):
            raise ValueError('ticket_index')
        return rows[index]

    def route(start, end, depart, mode):
        if mode not in traveler.request.get('local_modes', ('metro', 'taxi', 'walk')):
            raise ValueError('local_mode')
        legs = traveler.route(start, end, depart, mode)
        if legs is None:
            raise ValueError('route_unavailable')
        return legs

    go, back = choice(outbound, draft['out']), choice(inbound, draft['back'])
    position = go['To']
    days = []
    for day, row in enumerate(plan_days, 1):
        activities = [traveler.ticket_activity(go)] if day == 1 else []
        now = go['_end'] if day == 1 else minutes(row['start'])
        for ref, kind, start, end, mode in row['stops']:
            place = records[ref]
            expected = {'attraction': 'attraction', 'accommodation': 'accommodation',
                        'lunch': 'restaurant', 'dinner': 'restaurant'}.get(kind)
            if place['kind'] != expected:
                raise ValueError('place_kind')
            target = place['name']
            legs = route(position, target, now, mode)
            if kind == 'accommodation':
                hotel = next((h for h in traveler.hotels if h['name'] == target), None)
                if hotel is None:
                    raise ValueError('room_requirements')
                activity = {'type': kind, 'position': target, 'price': hotel['price'],
                            'cost': hotel['price'] * hotel['_rooms'], 'rooms': hotel['_rooms'],
                            'room_type': int(hotel['numbed']), 'start_time': start, 'end_time': end,
                            'transports': legs}
            else:
                activity = traveler.poi_activity({'name': target, 'price': place['price']['value']},
                                                 kind, minutes(start), minutes(end), legs)
            activities.append(activity)
            now, position = minutes(end), target
        if day == traveler.days:
            activities.append(traveler.ticket_activity(back, route(position, back['From'], now, draft['return_mode'])))
        elif not activities or activities[-1]['type'] != 'accommodation':
            raise ValueError('missing_overnight_location')
        days.append({'day': day, 'activities': activities})
    return {'people_number': traveler.people, 'start_city': traveler.request['start_city'],
            'target_city': traveler.city, 'itinerary': days}


def run_direct(spec, snapshot, traveler, packet, figures, provider, out, env, facts):
    started = perf_counter()
    reply = provider(DIRECT_PROMPT, packet, max_tokens=TOKENS, timeout=MODEL_TIMEOUT)
    save(out / 'MODEL_REPLY.json', asdict(reply))
    error = reply.error
    candidate = []
    if not error:
        try:
            traveler.deadline = perf_counter() + SECONDS
            plan = normalize_direct(reply.draft, *figures, traveler)
            candidate.append({'id': 'direct', 'itinerary': plan, 'source': 'direct_model'})
        except (ValueError, TypeError, KeyError, SearchBudgetExpired) as exc:
            error = 'invalid_route_or_fields:' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    archive = {'schema_version': 'sketch_trial_v1', 'snapshot_id': snapshot['snapshot_id'], 'archive': candidate}
    assessed = evaluate_archive(spec, snapshot, archive, env, facts)
    save(out / 'CANDIDATE_ARCHIVE.json', archive)
    save(out / 'PLAN_ASSESSMENT.json', assessed)
    elapsed = perf_counter() - started
    result = {'state': 'complete' if assessed['verified_count'] else error or 'independent_verification_failed',
              'elapsed_seconds': elapsed, 'first_visible_seconds': elapsed if assessed['verified_count'] else None,
              'model': {'requests': reply.attempts, 'usage': reply.usage, 'name': reply.model, 'error': error,
                        'seconds': reply.elapsed_seconds}}
    return save(out / 'OUTCOME.json', result)


def summarize(out, status):
    path = out / 'PLAN_ASSESSMENT.json'
    summary = {k: status.get(k) for k in ('state', 'elapsed_seconds', 'first_visible_seconds', 'model')}
    if path.is_file():
        result = read(path)
        summary['verified'] = result['verified_count']
        summary['alternatives'] = len(result['selection']['alternatives'])
        summary['rejections'] = dict(Counter(i['code'] for c in result['candidates']
                                              for i in c['validation_report']['issues']))
        entry = next((c for c in result['candidates'] if c['id'] == result['selection']['recommended_id']), None)
        if entry:
            summary.update(recommended_id=entry['id'], source=entry['source'], metrics=entry['quality_vector'],
                           goals=[{k: g[k] for k in ('field', 'value', 'status')} for g in entry['goals']])
    return summary


def blind_view(folder, pick):
    archive = read(folder / 'CANDIDATE_ARCHIVE.json')['archive']
    plan = next(c['itinerary'] for c in archive if c['id'] == pick)
    evaluation = read(folder / 'PLAN_ASSESSMENT.json')['candidates']
    q = next(c['quality_vector'] for c in evaluation if c['id'] == pick)
    compact = [{'day': day['day'], 'activities': [{k: a[k] for k in
                ('type', 'position', 'start', 'end', 'start_time', 'end_time') if k in a}
                for a in day['activities']]} for day in plan['itinerary']]
    return {'days': compact, 'metrics': {k: q[k] for k in ('total_cost_cny', 'cost_breakdown_cny',
             'city_transit_minutes', 'access_walk_minutes', 'visit_minutes')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ask-key', action='store_true')
    parser.add_argument('--program-only', action='store_true')
    args = parser.parse_args()
    manifest = read(ROOT / 'MANIFEST.json')
    if manifest != frozen_manifest():
        parser.error('The pre-registered cases or parameters differ from the runner')
    key = getpass('DeepSeek key: ') if args.ask_key else None
    provider = partial(request_deepseek_json, api_key=key, capture_invalid=True)
    out = fresh(args.out)
    save(out / 'MANIFEST.json', manifest)
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    specs = select_cases('frozen30')
    rows, review, key_sheet = [], [], []
    random = Random(manifest['review_seed'])
    for index, cid in enumerate(CASES):
        spec = specs[cid]
        folder = out / cid
        request, unsupported = constructor_input(spec)
        ready = spec.get('ready') and not unsupported and request.get('local_modes') != ()
        if ready:
            tick = perf_counter()
            snapshot = facts.trip_snapshot(spec)
            prepare_snapshot_routes(request, env, facts)
            traveler = Constructor(request, env, SECONDS, CANDIDATES)
            packet, *figures = model_facts(snapshot, traveler)
            save(folder / 'SHARED_MODEL_FACTS.json', packet)
            preparation_seconds = perf_counter() - tick
        else:
            snapshot = None
            preparation_seconds = 0.
        order = ARMS[index % 3:] + ARMS[:index % 3]
        results = {}
        for arm in (('program',) if args.program_only else order):
            target = folder / arm
            if arm == 'direct_model' and ready:
                status = run_direct(spec, snapshot, traveler, packet, figures, provider, target, env, facts)
            elif arm == 'direct_model':
                status = {'state': 'needs_confirmation', 'model': {'requests': 0}}
                save(target / 'OUTCOME.json', status)
            else:
                def same_facts(prompt, _payload, **options):
                    return provider(prompt, packet, **options)
                status = run_trip_plan(spec, target, env, facts, model=arm == 'model_sketch',
                                       seconds=SECONDS, candidates=CANDIDATES, saved_snapshot=snapshot,
                                       sketch_provider=same_facts if arm == 'model_sketch' and ready else None)
            results[arm] = summarize(target, status)
            if arm != 'program' and status.get('model', {}).get('error') in (
                    'missing_model_credentials', 'AuthenticationError'):
                print('Model access unavailable:', status['model']['error'], flush=True)
            print(cid, arm, status['state'], results[arm].get('verified', 0), flush=True)
        for left, right in zip(ARMS, ARMS[1:]):
            if all(results.get(arm, {}).get('verified') for arm in (left, right)):
                sides = [left, right]
                random.shuffle(sides)
                pair_id = 'review-' + str(len(review) + 1)
                review.append({'id': pair_id, 'request': spec['raw_text'],
                               **{label: blind_view(folder / arm, results[arm]['recommended_id'])
                                  for label, arm in zip(('A', 'B'), sides)}})
                key_sheet.append({'id': pair_id, 'case': cid, 'A': sides[0], 'B': sides[1]})
        rows.append({'id': cid, 'order': [a for a in order if a in results],
                     'common_preparation_seconds': preparation_seconds, 'arms': results})
        save(out / 'SUMMARY.json', {'cases': rows})
        save(out / 'BLIND_PAIRS.json', review)
        save(out / 'BLIND_KEY.json', key_sheet)


if __name__ == '__main__':
    main()
