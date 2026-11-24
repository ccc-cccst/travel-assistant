"""Pair program planning with program plus model sketches on the old 30 requests."""

import argparse
from dataclasses import asdict, replace
from getpass import getpass
from hashlib import sha256
from inspect import getfile
import json
import os
from pathlib import Path
from random import Random
from time import perf_counter

from compare_place_references import compare_outcome
from compare_planning_models import (
    CANDIDATES, MODEL, MODEL_TIMEOUT, ROOT, SECONDS, TOKENS, model_facts, select_cases,
)
from replay_short_sketch_refs import SHORT_ID_INSTRUCTION, restore, short_payload
from replay_integrated_planning import P007
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import display_days, run_trip_plan
from chinatravel.personalization.planning_compat import constructor_input, prepare_snapshot_routes
from chinatravel.personalization.tripspec import ModelReply, request_deepseek_json
from chinatravel.personalization.world_facts import WorldFacts
from chinatravel.planning.engine import Constructor


ARMS = ('program', 'model_sketch')
LABELS = {'train': '火车', 'airplane': '航班', 'accommodation': '住宿',
          'attraction': '景点', 'lunch': '午餐', 'dinner': '晚餐'}
PENDING_LABELS = {'current_ticket_and_room_availability': '当日票和酒店余房',
                  'date_specific_prices_and_opening': '指定日期的价格和营业时间',
                  'inside_attraction_walking': '景点内部步行',
                  'actual_room_type_and_occupancy': '实际房型与可入住人数'}
REVIEW_SEED = 20261122
SEALED_MANIFEST = ROOT.parent / 'P015_planning_coverage/FINAL_HOLDOUT_MANIFEST.json'


def sealed_cases():
    manifest = read(SEALED_MANIFEST)
    source = Path(__file__).resolve().parents[1] / manifest['input_source']
    content = source.read_bytes()
    if source != P007 or sha256(content).hexdigest() != manifest['input_sha256']:
        raise ValueError('Sealed request data has changed')
    cases = manifest['cases']
    all_specs = {}
    for line in content.decode('utf-8-sig').splitlines():
        row = json.loads(line)
        if row['cohort'] == 'holdout':
            all_specs[row['case']] = row['variants']['C']
    selected = {}
    for case in cases:
        case_id = case['id']
        spec = all_specs[case_id]
        if sha256(spec['raw_text'].encode()).hexdigest() != case['text_sha256']:
            raise ValueError(f'Sealed case text changed for {case_id}')
        selected[case_id] = spec
    if len(selected) != 30 or selected.keys() & select_cases('frozen30').keys():
        raise ValueError('Sealed request IDs conflict with the 30-case development suite')
    return selected


def source_hashes():
    repo = Path(__file__).resolve().parents[1]
    paths = [repo / 'tools' / name for name in (
        'compare_planning_regression.py', 'compare_place_references.py',
        'compare_planning_models.py', 'replay_short_sketch_refs.py',
    )]
    paths += sorted((repo / 'chinatravel/personalization').glob('*.py'))
    paths += sorted(Path(getfile(Constructor)).parent.glob('*.py'))
    return {Path(os.path.relpath(path, repo)).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in paths}


def archived_reply(reuse, case_id, ref_format, snapshot, packet):
    if reuse is None:
        return None
    folder = reuse / case_id / ref_format
    raw = folder / 'RAW_MODEL_REPLY.json'
    if not raw.is_file() or read(folder / 'WORLD_FACTS.json') != snapshot:
        return None
    if read(folder / 'MODEL_FACTS.json') != packet:
        return None
    reply = ModelReply(**read(raw))
    if reply.error or reply.draft is None:
        return None
    return reply


def review_card(folder, spec, card):
    candidate = next(c for c in read(folder / 'CANDIDATE_ARCHIVE.json')['archive'] if c['id'] == card['id'])
    return {
        **{k: card[k] for k in ('metrics', 'goals', 'pending')},
        'days': display_days(candidate['itinerary'], spec, ordered=True),
    }


def review_markdown(pairs):
    lines = ['# 原题行程盲评', '', '请选 A、B、差不多、两个都不愿采用或资料不足，并写一句理由。',
             '价格和交通时长来自本地快照。固定预约和城际时刻以各方案中列出的信息为准。']
    for pair in pairs:
        lines += ['', f"## {pair['id']}", '',
                  *(f'> {line}' for line in pair['request'].splitlines())]
        for label in ('A', 'B'):
            card = pair[label]
            q = card['metrics']
            lines += ['', f'### 方案 {label}', '',
                      f"参考总价 {q['total_cost_cny']:,.2f} 元 · 市内接驳 {q['city_transit_minutes']} 分钟"
                      f" · 其中步行 {q['access_walk_minutes']} 分钟 · 游览 {q['visit_minutes']} 分钟", '']
            for day in card['days']:
                activities = []
                for stop in day['activities']:
                    detail = f"{LABELS.get(stop['kind'], stop['kind'])} {stop['name']}"
                    if stop.get('time'):
                        detail += ' ' + stop['time']
                    if stop['incoming_minutes'] >= 30:
                        detail += f"（前段接驳 {stop['incoming_minutes']} 分钟）"
                    activities.append(detail)
                lines.append(f"- 第 {day['day']} 天 · {' → '.join(activities)}")
            if card['pending']:
                pending = '、'.join(PENDING_LABELS.get(code, code) for code in card['pending'])
                lines += ['', f'待核实 · {pending}']
        lines += ['', '选择和理由']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--refs', choices=('long_ids', 'short_ids'), required=True)
    parser.add_argument('--reuse', type=Path)
    parser.add_argument('--sealed', action='store_true')
    parser.add_argument('--ask-key', action='store_true')
    args = parser.parse_args()
    key = getpass('DeepSeek key: ') if args.ask_key else os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        parser.error('Set DEEPSEEK_API_KEY or use --ask-key')
    if args.sealed and args.reuse:
        parser.error('The sealed suite uses fresh model responses')
    out = fresh(args.out)
    config = {
        'suite': 'sealed30' if args.sealed else 'frozen30', 'arms': ARMS, 'model': MODEL, 'refs': args.refs,
        'planner_seconds': SECONDS, 'candidate_cap': CANDIDATES, 'request_timeout_seconds': MODEL_TIMEOUT,
        'output_tokens_cap': TOKENS, 'review_seed': REVIEW_SEED, 'reuse': args.reuse,
    }
    if args.sealed:
        save(out / 'FREEZE.json', {**config, 'data': str(DATA), 'source_sha256': source_hashes(),
                                   'policy': 'one_model_request_on_supported_cases'})
    specs = sealed_cases() if args.sealed else select_cases('frozen30')
    save(out / 'MANIFEST.json', {
        **config,
        'cases': [{'id': cid, 'text_sha256': sha256(spec['raw_text'].encode()).hexdigest()}
                  for cid, spec in specs.items()],
    })
    tick = perf_counter()
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    initialization = perf_counter() - tick
    rng = Random(REVIEW_SEED)
    rows, review, review_key = [], [], []
    for index, (cid, spec) in enumerate(specs.items()):
        case = out / cid
        order = ARMS if index % 2 == 0 else ARMS[::-1]
        snapshot = packet = aliases = None
        prepared = 0.
        reply_source = None
        archived_usage = {}
        archived_model_seconds = None
        request, unsupported = constructor_input(spec) if spec.get('ready') else ({}, [])
        if request and not unsupported and request.get('local_modes') != ():
            tick = perf_counter()
            snapshot = facts.trip_snapshot(spec)
            prepare_snapshot_routes(request, env, facts)
            packet, *_ = model_facts(snapshot, Constructor(request, env, SECONDS, CANDIDATES))
            if args.refs == 'short_ids':
                packet, aliases = short_payload(packet)
                save(case / 'PLACE_ID_MAP.json', aliases)
            prepared = perf_counter() - tick

        def provider(prompt, _payload, **options):
            nonlocal reply_source, archived_usage, archived_model_seconds
            if packet is None:
                raise ValueError(f'No shared facts for {cid}')
            if args.refs == 'short_ids':
                prompt += SHORT_ID_INSTRUCTION
            target = case / 'model_sketch'
            save(target / 'MODEL_FACTS.json', packet)
            reply = archived_reply(args.reuse, cid, args.refs, snapshot, packet)
            if reply is None:
                reply_source = 'live'
                reply = request_deepseek_json(prompt, packet, api_key=key, capture_invalid=True, **options)
            else:
                reply_source = 'archived_reply'
                archived_usage = reply.usage
                archived_model_seconds = reply.elapsed_seconds
            save(target / 'RAW_MODEL_REPLY.json', asdict(reply))
            if reply_source == 'archived_reply':
                reply = replace(reply, attempts=0, elapsed_seconds=0, usage={})
            if aliases is not None:
                reply.draft = restore(reply.draft, aliases)
            return reply

        results = {}
        for arm in order:
            folder = case / arm
            use_model = arm == 'model_sketch'
            status = run_trip_plan(spec, folder, env, facts, model=use_model, seconds=SECONDS,
                                   candidates=CANDIDATES, saved_snapshot=snapshot,
                                   sketch_provider=provider if use_model else None)
            results[arm] = compare_outcome(folder, status)
            results[arm]['reply_source'] = reply_source if use_model else None
            if use_model and reply_source == 'archived_reply':
                results[arm].update(archived_usage=archived_usage,
                                    archived_model_seconds=archived_model_seconds)
            print(cid, arm, status['state'], results[arm]['verified'],
                  results[arm].get('model_error'), flush=True)
        if all(results[arm]['verified'] for arm in ARMS):
            cards = {arm: read(case / arm / 'PLAN_VIEW.json')['candidates'][0] for arm in ARMS}
            if any(cards[ARMS[0]][k] != cards[ARMS[1]][k] for k in ('days', 'metrics', 'goals')):
                sides = list(ARMS)
                rng.shuffle(sides)
                pair_id = f"review-{len(review) + 1:02}"
                review.append({'id': pair_id, 'request': spec['raw_text'],
                               **{label: review_card(case / arm, spec, cards[arm])
                                  for label, arm in zip(('A', 'B'), sides)}})
                review_key.append({'id': pair_id, 'case': cid, 'A': sides[0], 'B': sides[1]})
        rows.append({'case': cid, 'order': order, 'shared_preparation_seconds': prepared, 'arms': results})
        save(out / 'SUMMARY.json', {'initialization_seconds': initialization, 'cases': rows})
        save(out / 'BLIND_PAIRS.json', review)
        save(out / 'BLIND_KEY.json', review_key)
        (out / 'BLIND_REVIEW.md').write_text(review_markdown(review), encoding='utf-8')


if __name__ == '__main__':
    main()
