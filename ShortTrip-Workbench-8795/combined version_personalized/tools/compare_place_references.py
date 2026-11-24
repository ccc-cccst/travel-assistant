"""Pair long and short place IDs on the frozen P020 original requests."""

import argparse
from dataclasses import asdict, replace
from getpass import getpass
from hashlib import sha256
import os
from pathlib import Path

from compare_planning_models import (
    CASES, CANDIDATES, ROOT, SECONDS, frozen_manifest, summarize,
)
from replay_short_sketch_refs import (
    CASES as REPLAY_CASES,
    SHORT_ID_INSTRUCTION,
    restore,
    short_payload,
)
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.planning_compat import constructor_input, prepare_snapshot_routes
from chinatravel.personalization.tripspec import ModelReply, request_deepseek_json
from chinatravel.personalization.world_facts import WorldFacts


ARMS = ('long_ids', 'short_ids')
P021 = ROOT.parent / 'P021_model_place_references/live_01'


def compare_outcome(folder, status):
    result = summarize(folder, status)
    result.update(timings=status['timings'], stop_reason=status.get('stop_reason'),
                  reason_code=status.get('reason_code'), verified=result.get('verified', 0))
    layouts_path = folder / 'TRIP_SKETCHES.json'
    if layouts_path.is_file():
        layouts = read(layouts_path)
        result.update(
            accepted_model_layouts=sum(s['source'] == 'model' for s in layouts['sketches']),
            model_rejections=layouts['model']['rejected'],
            model_error=layouts['model']['error'],
            model_seconds=layouts['model']['elapsed_seconds'],
        )
    assessment_path = folder / 'PLAN_ASSESSMENT.json'
    if assessment_path.is_file():
        assessment = read(assessment_path)
        result['verified_model_candidates'] = sum(
            c['source'] == 'model' and c['validation_report']['passed']
            for c in assessment['candidates']
        )
        result['alternative_ids'] = [c['id'] for c in assessment['selection']['alternatives']]
    return result


def saved_reply(case_id, arm):
    if arm == 'short_ids':
        return ModelReply(**read(P021 / case_id / 'RAW_MODEL_REPLY.json'))
    reply = read(ROOT / 'live_02' / case_id / 'model_sketch/TRIP_SKETCHES.json')['model']
    return ModelReply(draft=reply['draft'], usage=reply['usage'], error=reply['error'],
                      model=reply['name'], invalid_content=reply.get('invalid_content'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--ask-key', action='store_true')
    mode.add_argument('--replay', action='store_true')
    args = parser.parse_args()
    manifest = frozen_manifest()
    if manifest != read(ROOT / 'MANIFEST.json'):
        parser.error('P020 frozen cases or settings differ')
    key = getpass('DeepSeek key: ') if args.ask_key else os.environ.get('DEEPSEEK_API_KEY')
    if not args.replay and not key:
        parser.error('Set DEEPSEEK_API_KEY or use --ask-key for a live run')
    case_ids = REPLAY_CASES if args.replay else CASES
    definitions = [row for row in manifest['cases'] if row['id'] in case_ids]
    out = fresh(args.out)
    save(out / 'MANIFEST.json', {**manifest, 'cases': definitions, 'arms': ARMS,
                                 'mode': 'archived_reply' if args.replay else 'live',
                                 'facts_source': 'P020_planning_model_comparison/live_02'})
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    rows = []
    sources = {row['id']: row['raw_sha256'] for row in definitions}
    for index, case_id in enumerate(case_ids):
        source = ROOT / 'live_02' / case_id
        baseline = source / 'model_sketch'
        spec = read(baseline / 'request.json')
        if sha256(spec['raw_text'].encode()).hexdigest() != sources[case_id]:
            raise ValueError(f'Frozen request text mismatch for {case_id}')
        facts_path = source / 'SHARED_MODEL_FACTS.json'
        snapshot = original = compact = aliases = None
        if facts_path.is_file():
            original = read(facts_path)
            compact, aliases = short_payload(original)
            snapshot = read(baseline / 'WORLD_FACTS.json')
            save(out / case_id / 'PLACE_ID_MAP.json', aliases)
            request, _ = constructor_input(spec)
            prepare_snapshot_routes(request, env, facts)
        order = ARMS if index % 2 == 0 else ARMS[::-1]
        results = {}
        for arm in order:
            folder = out / case_id / arm

            def provider(prompt, _payload, **options):
                packet = original
                if arm == 'short_ids':
                    prompt += SHORT_ID_INSTRUCTION
                    packet = compact
                if packet is None:
                    raise ValueError(f'No frozen model facts for {case_id}')
                save(folder / 'MODEL_FACTS.json', packet)
                reply = (saved_reply(case_id, arm) if args.replay else
                         request_deepseek_json(prompt, packet, api_key=key,
                                               capture_invalid=True, **options))
                save(folder / 'RAW_MODEL_REPLY.json', asdict(reply))
                if args.replay:
                    reply = replace(reply, attempts=0, elapsed_seconds=0, usage={})
                if arm == 'short_ids':
                    reply.draft = restore(reply.draft, aliases)
                return reply

            status = run_trip_plan(spec, folder, env, facts, model=True, seconds=SECONDS,
                                   candidates=CANDIDATES, sketch_provider=provider, saved_snapshot=snapshot)
            results[arm] = compare_outcome(folder, status)
            print(case_id, arm, status['state'], results[arm].get('accepted_model_layouts', 0),
                  results[arm]['verified'], results[arm].get('model_error'), flush=True)
        rows.append({'case': case_id, 'order': order, 'arms': results})
        save(out / 'SUMMARY.json', {'mode': 'archived_reply' if args.replay else 'live', 'cases': rows})


if __name__ == '__main__':
    main()
