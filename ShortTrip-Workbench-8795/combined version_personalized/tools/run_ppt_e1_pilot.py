"""Run the non-workbench arms of the P071 adapter pilot on the frozen cases."""
import argparse
from functools import partial
from pathlib import Path
from time import perf_counter
import traceback

import check_ppt_scenario as native
import compare_planning_models as direct
from chinatravel.personalization.local_model import request_local_json
from chinatravel.personalization.tripspec import ModelReply, evaluate_draft


def case_args(case, date):
    core = case['corpus_core']
    return {'start_city': core['origin_city'], 'target_city': core['destination_city'],
            'days': core['days'], 'people_number': core['people'], 'start_date': date}


def shortened_packet(packet):
    """Keep the same facts with compact place and ticket IDs."""
    result = {**packet, 'places': []}
    ids = {}
    for index, (ref, *fields) in enumerate(packet['places'], 1):
        alias = f'P{index}'
        ids[alias] = ref
        result['places'].append([alias, *fields])
    return result, ids


def select_facts(facts, spec, traveler, search_reply):
    snapshot = facts.trip_snapshot(spec, limit=12)
    packet, records, outbound, inbound = direct.model_facts(snapshot, traveler)
    searched = {}
    for kind in ('attraction', 'restaurant', 'accommodation'):
        terms = (search_reply.draft or {}).get(kind, [])
        if not isinstance(terms, list):
            continue
        rows, _ = facts._catalog(snapshot['destination'], kind)
        matched = []
        for term in terms[:6]:
            if not isinstance(term, str) or not term.strip() or len(term) > 60:
                continue
            for row in rows:
                point = facts._place(row)
                if (not point['planning_catalog'] or point['id'] in records or not point['location']
                        or not point['price']['usable_for_snapshot_budget']):
                    continue
                values = [point['name'], *point['tags']['values']]
                if not any(term.casefold() in str(value).casefold() for value in values):
                    continue
                records[point['id']] = point
                pos = point['location']
                packet['places'].append([
                    point['id'], point['name'], point['tags']['values'],
                    [round(pos['lat'], 3), round(pos['lon'], 3)],
                    point['opening']['windows_minutes'], point['price']['value'],
                    point['price']['unit'], point['benchmark_bed_count'],
                ])
                matched.append(point['id'])
                if len(matched) == 12:
                    break
            if len(matched) == 12:
                break
        searched[kind] = matched
    packet['outbound'] = packet['outbound'][:18]
    packet['return'] = packet['return'][:18]
    return snapshot, packet, (records, outbound[:18], inbound[:18]), searched


def request_adapter(provider, ids, reply_payload, started, seconds):
    def submit(prompt, _packet, *, max_tokens, timeout):
        remaining = seconds - (perf_counter() - started)
        if remaining <= 0:
            return ModelReply(error='outer_timeout')
        reply = provider(prompt + '\n请按照用户原话完成规划，地点只引用 P 开头的短编号。',
                         reply_payload, max_tokens=max_tokens, timeout=min(timeout, remaining))
        if isinstance(reply.draft, dict):
            raw_draft = reply.draft
            days = raw_draft.get('days')
            if isinstance(days, list):
                # Keep originals in MODEL_REPLY.json and convert only the known short IDs.
                import copy
                normalized = copy.deepcopy(raw_draft)
                for day in normalized.get('days', []):
                    if not isinstance(day, dict) or not isinstance(day.get('stops'), list):
                        continue
                    for stop in day['stops']:
                        if isinstance(stop, list) and stop and isinstance(stop[0], str):
                            stop[0] = ids.get(stop[0], stop[0])
                reply.draft = normalized
                reply_payload['_saved_draft'] = raw_draft
        return reply
    return submit


def model_arm(case, folder, model, date, repair, seconds):
    started = perf_counter()
    folder.mkdir(parents=True)
    env = direct.environment(direct.DATA)
    facts = direct.WorldFacts.configured(direct.DATA)
    raw = case['raw_text']
    form = case_args(case, date)
    # Core trip fields are benchmark metadata needed by the original ChinaTravel adapter.
    # Raw constraints remain in the prompt and are checked separately from these fields.
    core_spec = evaluate_draft('', form, env.support_cities, ModelReply())
    request, _ = direct.constructor_input(core_spec)
    direct.prepare_snapshot_routes(request, env, facts)
    traveler = direct.Constructor(request, env, seconds, 1)
    provider = request_local_json if model == 'qwen' else partial(
        direct.request_deepseek_json, capture_invalid=True)
    direct.TOKENS = 4096
    direct.MODEL_TIMEOUT = 95 if model == 'qwen' else 60
    direct.SECONDS = 60
    from dataclasses import asdict
    search_reply = provider(
        '从原话选择需要查询的景点、餐馆、酒店的准确名称或属性关键词。'
        '只输出 JSON 对象，键为 attraction、restaurant、accommodation，值为字符串数组。'
        '每类最多6项，没有要求时给空数组。不要安排行程。',
        {'raw_text': raw, 'requested_date': date}, max_tokens=384, timeout=40)
    direct.save(folder / 'LOOKUP_REPLY.json', asdict(search_reply))
    snapshot, packet, figures, hits = select_facts(facts, core_spec, traveler, search_reply)
    packet, ids = shortened_packet(packet)
    packet.update(raw_text=raw, departure_date=date)
    direct.save(folder / 'FACTS_INPUT.json', packet)
    direct.save(folder / 'FACTS_LOOKUP.json', hits)

    def trial(out, payload):
        out.mkdir()
        submit = request_adapter(provider, ids, payload, started, seconds)
        status = direct.run_direct(core_spec, snapshot, traveler, payload, figures, submit, out, env, facts)
        original = payload.pop('_saved_draft', None)
        if original is not None:
            direct.save(out / 'ORIGINAL_MODEL_DRAFT.json', original)
        return direct.summarize(out, status)

    draft_output = folder / 'draft'
    initial = trial(draft_output, dict(packet))
    first = perf_counter() - started if initial.get('verified') else None
    status = initial
    if repair and not initial.get('verified'):
        reasons = direct.read(draft_output / 'PLAN_ASSESSMENT.json')
        issues = [issue for candidate in reasons['candidates']
                  for issue in candidate['validation_report']['issues']]
        previous = draft_output / 'ORIGINAL_MODEL_DRAFT.json'
        revised = {**packet, 'previous_draft': direct.read(previous) if previous.is_file() else None,
                   'verifier_issues': issues or [initial['state']],
                   'repair_instruction': '按这些问题修正，并保留用户原话中的要求。'}
        status = trial(folder / 'repair', revised)
        if status.get('verified'):
            first = perf_counter() - started
    calls = [asdict(search_reply), direct.read(draft_output / 'MODEL_REPLY.json')]
    if (folder / 'repair' / 'MODEL_REPLY.json').is_file():
        calls.append(direct.read(folder / 'repair' / 'MODEL_REPLY.json'))
    result = {**status, 'case': case['case'], 'elapsed_seconds': round(perf_counter() - started, 3),
              'first_common_check_seconds': first, 'model_calls': sum(call['attempts'] for call in calls),
              'call_usage': [call['usage'] for call in calls], 'initial_state': initial['state'],
              'evaluation_scope': 'snapshot feasibility, with raw-text desk check still pending',
              'fact_tool_scope': 'fixed snapshot with model-directed keyword lookup and bounded lists'}
    direct.save(folder / 'row.json', result)
    print(case['case'], model, folder.parent.name, result['state'], result['elapsed_seconds'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', choices=('deepseek', 'qwen'), required=True)
    parser.add_argument('--arm', choices=('native', 'direct', 'repair'), required=True)
    parser.add_argument('--case')
    parser.add_argument('--date', default='2026-11-22')
    parser.add_argument('--seconds', type=int, default=300)
    args = parser.parse_args()
    output = args.output.resolve()
    cases = native.load(args.manifest)['cases']
    cases = [case for case in cases if not args.case or args.case == case['case']]
    if not cases:
        parser.error('case not found in manifest')
    if args.arm == 'native':
        if len(cases) != 1:
            parser.error('native uses one isolated process per case')
        case = cases[0]
        query = {**case_args(case, args.date), 'uid': 'p071_' + case['case'],
                 'nature_language': case['raw_text']}
        native.native(case['case'], args.seconds, '', True, query=query, provider=args.model,
                      effective_path=native.OUT / 'EFFECTIVE_DATA.pkl', output=output)
        return
    for case in cases:
        folder = output / (args.arm + '_' + args.model) / case['case']
        if folder.exists():
            raise FileExistsError(folder)
        try:
            model_arm(case, folder, args.model, args.date, args.arm == 'repair', args.seconds)
        except Exception as exc:
            folder.mkdir(parents=True, exist_ok=True)
            direct.save(folder / 'row.json', {'case': case['case'], 'state': 'adapter_error',
                                             'error': type(exc).__name__, 'message': str(exc)[:350]})
            (folder / 'trace.txt').write_text(traceback.format_exc(), encoding='utf-8')
            print(case['case'], 'adapter_error', type(exc).__name__, flush=True)


if __name__ == '__main__':
    main()
