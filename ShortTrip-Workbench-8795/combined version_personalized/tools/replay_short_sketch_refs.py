"""Try explicit short place IDs on the three P020 cases with rejected model layouts."""

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from functools import partial
from getpass import getpass
from pathlib import Path

from compare_planning_models import CANDIDATES, ROOT, SECONDS, summarize
from chinatravel.personalization.cli import DATA, environment, fresh, read, save
from chinatravel.personalization.integrated_planning import run_trip_plan
from chinatravel.personalization.trip_sketch import PLACE_LISTS
from chinatravel.personalization.tripspec import request_deepseek_json
from chinatravel.personalization.world_facts import WorldFacts


CASES = ('easy_61', 'easy_279', 'human_85')
PREFIXES = {'attraction': 'A', 'restaurant': 'R', 'accommodation': 'H'}
SHORT_ID_INSTRUCTION = ('\n地点使用首列给出的完整短编号，例如 A1、R1、H1。'
                        '不要给短编号附加类别、城市或冒号。')


def short_payload(packet):
    payload = deepcopy(packet)
    counts, ids = Counter(), {}
    for place in payload['places']:
        kind = place[0].split(':', 1)[0]
        counts[kind] += 1
        alias = f'{PREFIXES[kind]}{counts[kind]}'
        ids[place[0]] = alias
        place[0] = alias
    for named in payload['named']:
        named['ids'] = [ids[ref] for ref in named['ids']]
    for appointment in payload['appointments']:
        appointment[1] = ids[appointment[1]]
    for appointment in payload['fixed_appointment_details']:
        appointment['id'] = ids[appointment['id']]
    return payload, {alias: ref for ref, alias in ids.items()}


def restore(draft, ids):
    if not isinstance(draft, dict) or not isinstance(draft.get('layouts'), list):
        return draft
    result = deepcopy(draft)
    for layout in result['layouts']:
        for day in layout if isinstance(layout, list) else ():
            if not isinstance(day, dict):
                continue
            for field in PLACE_LISTS:
                if isinstance(day.get(field), list):
                    day[field] = [ids.get(ref, ref) if isinstance(ref, str) else ref for ref in day[field]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ask-key', action='store_true')
    args = parser.parse_args()
    key = getpass('DeepSeek key: ') if args.ask_key else None
    provider = partial(request_deepseek_json, api_key=key, capture_invalid=True)
    out = fresh(args.out)
    env, facts = environment(DATA), WorldFacts.configured(DATA)
    rows = []
    for case in CASES:
        source = ROOT / 'live_02' / case
        folder = out / case
        packet, ids = short_payload(read(source / 'SHARED_MODEL_FACTS.json'))
        save(folder / 'MODEL_FACTS.json', packet)
        save(folder / 'PLACE_ID_MAP.json', ids)

        def with_refs(prompt, _payload, **options):
            reply = provider(prompt + SHORT_ID_INSTRUCTION, packet, **options)
            save(folder / 'RAW_MODEL_REPLY.json', asdict(reply))
            reply.draft = restore(reply.draft, ids)
            return reply

        baseline = source / 'model_sketch'
        result = run_trip_plan(read(baseline / 'request.json'), folder, env, facts, model=True,
                               seconds=SECONDS, candidates=CANDIDATES, sketch_provider=with_refs,
                               saved_snapshot=read(baseline / 'WORLD_FACTS.json'))
        row = {'case': case}
        for arm, path, status in (('original', baseline, read(baseline / 'STATUS.json')), ('short_ids', folder, result)):
            layouts = read(path / 'TRIP_SKETCHES.json')
            row[arm] = {**summarize(path, status),
                        'accepted_model_layouts': sum(s['source'] == 'model' for s in layouts['sketches']),
                        'rejected_layouts': layouts['model']['rejected']}
        rows.append(row)
        save(out / 'SUMMARY.json', {'cases': rows})
        print(case, [(a, row[a]['accepted_model_layouts'], row[a]['state']) for a in ('original', 'short_ids')],
              flush=True)


if __name__ == '__main__':
    main()
