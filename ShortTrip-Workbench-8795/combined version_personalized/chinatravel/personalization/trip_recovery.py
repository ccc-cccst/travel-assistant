"""Try to deliver a verified plan after the original trip request fails."""

from copy import deepcopy
from math import ceil
import re
from time import perf_counter

from .cli import read, save
from .integrated_planning import run_trip_plan
from .planning_compat import DEFAULT_LOCAL_MODES, local_mode_options
from .tripspec import normalize_saved_spec, resolve_conflicts
from chinatravel.planning.engine import clock


RECOVERY_SECONDS = 12
RETRY_CODES = {'search_exhausted', 'search_timeout', 'verification_failed'}
ATTEMPT_IDS = ('attempt_1', 'attempt_2')
TICKET_SCOPE = {'outbound', 'return', 'round_trip', 'intercity_trip', 'total_trip'}
LOCKED_TEXT = re.compile(r'已.{0,4}(?:订|买|出票)|不可更改|不能改|不能换|不接受改|绝不能|只接受|不坐火车|不坐飞机')
BUDGET_LOCKED_TEXT = re.compile(
    r'预算.{0,16}(?:不能超|不可超|不得超|绝不超|不能调|不加钱|一分.{0,2}不加)'
    r'|(?:绝对|绝不能|绝不可).{0,8}超')


def _changed(spec, item, value, label, scope=None):
    revised = deepcopy(spec)
    change = {'field': item['field'], 'scope': scope or item['scope'], 'before': item['value'],
              'after': value, 'label': label}
    replacement = revised['items'][spec['items'].index(item)]
    replacement['additional_sources'] = [*replacement.get('additional_sources', []), {'type': 'recovery_assumption'}]
    if scope and item['scope'] != scope:
        # A round-trip restriction still applies to the other direction.
        replacement['scope'] = 'return' if scope == 'outbound' else 'outbound'
        revised['items'].append({**replacement, 'value': value, 'scope': scope})
        replacement['additional_sources'] = item.get('additional_sources', [])[:]
    else:
        replacement['value'] = value
    revised['request_revision'] += 1
    return revised, change


def _higher_budget(spec):
    limits = [i for i in spec['items'] if i['field'] == 'budget' and i['strength'] == 'hard'
              and i['scope'] == 'total_trip' and i['operator'] == 'lte' and i['unit'] in ('CNY/group', 'CNY/person')]
    if len(limits) != 1 or BUDGET_LOCKED_TEXT.search(spec.get('raw_text') or ''):
        return None
    limit, = limits
    if not isinstance(limit['value'], (float, int)) or limit['value'] <= 0:
        return None
    label = '人均预算上限（元）' if limit['unit'] == 'CNY/person' else '整团预算上限（元）'
    return _changed(spec, limit, ceil(limit['value'] * 1.25), label)


def _proposals(spec, failure, snapshot, try_budget_alternatives=False):
    code = failure.get('reason_code') or failure['state']
    detail = failure.get('evidence', {})
    if not spec['ready']:
        issues = spec['issues']
        if len(issues) == 1 and issues[0]['field'] in ('days', 'people', 'start_date'):
            issue = issues[0]
            for index, choice in enumerate(issue.get('choices', [])[:2]):
                revised = resolve_conflicts(spec, {issue['id']: index})
                if revised['ready']:
                    revised['items'][-1]['additional_sources'][-1]['type'] = 'recovery_assumption'
                    yield revised, {'field': issue['field'], 'scope': choice['scope'], 'before':
                                    [c['value'] for c in issue['choices']], 'after': choice['value'],
                                    'label': choice['label']}, '按这一种解释试排', False
        return
    if code in RETRY_CODES:
        yield spec, None, '优先试排低费用、方便接驳的组合，沿用原要求', True
        hotel_items=[i for i in spec['items'] if i['field']=='hotel_feature' and i['strength']=='hard']
        if hotel_items and any(d.get('reasons',{}).get('no_compatible_hotel')
                               for d in detail.get('search_diagnostics',[])):
            revised=deepcopy(spec)
            revised['request_revision']+=1
            parts=[]
            for i in revised['items']:
                if i in hotel_items:
                    parts.append({'field':i['field'],'scope':i['scope'],'label':'酒店设施需自行核实',
                                  'before':i['value'],'after':'保留为偏好，预订前确认'})
                    i.update(strength='soft',operator='prefer')
                    i.setdefault('additional_sources',[]).append({'type':'recovery_assumption'})
            yield revised, {'parts':parts}, '保留其他要求，试排设施待确认的酒店', True
            return
        if try_budget_alternatives and any(
                {'fixed_budget', 'actual_budget'} & d.get('reasons', {}).keys()
                for d in detail.get('search_diagnostics', [])):
            if proposal := _higher_budget(spec):
                yield *proposal, '以增加后的预算试排完整行程', True
    if code=='visit_evidence_unavailable' and snapshot:
        missing={i['id']:i for i in detail['unresolved_visits']}
        revised=deepcopy(spec)
        parts=[]
        for snap_item, item in zip(snapshot['requirements'], revised['items']):
            if snap_item['id'] not in missing:
                continue
            matches=[r['name'] for g in snapshot['place_groundings'] if g['requirement_id']==snap_item['id']
                     for r in g['records'] if r['planning_catalog'] and r['routable_in_snapshot']]
            if not matches:
                return
            replacement=matches[0]
            parts.append({'field':'attraction','scope':item['scope'],'label':'确认具体景点',
                          'before':item['value'],'after':replacement})
            item['value']=replacement
            item.setdefault('additional_sources',[]).append({'type':'recovery_assumption'})
        if parts:
            revised['request_revision']+=1
            yield revised, {'parts':parts}, '按目录中的具体景点试排，须确认后采用', False
            if try_budget_alternatives and (budget_trial:=_higher_budget(revised)):
                adjusted,budget_change=budget_trial
                adjusted['request_revision']=revised['request_revision']
                yield adjusted, {'parts':[*parts,budget_change]}, '保留景点和高铁要求，试排较高预算的方案', False
    if LOCKED_TEXT.search(spec.get('raw_text') or ''):
        return
    if code == 'lodging_evidence_unavailable' and snapshot and len(detail.get('unresolved_hotel_ids', [])) == 1:
        identifier, = detail['unresolved_hotel_ids']
        item = next((a for a, row in zip(spec['items'], snapshot['requirements']) if row['id']==identifier), None)
        names = dict.fromkeys(p['name'] for g in detail['hotel_groundings'] if g.get('modifiers')
                              for p in g['records'] if p['planning_catalog'] and p['routable_in_snapshot'])
        if item:
            for name in list(names)[:2]:
                yield *_changed(spec, item, name, '确认具体酒店分店'), '按这个分店试排，须确认后采用', False
    if code == 'same_day_ticket_window':
        days = next(i for i in spec['items'] if i['field'] == 'days')
        revised, change = _changed(spec, days, 2, '旅行天数')
        yield revised, change, '延长为两天，核算住宿、用餐和整趟费用', False
        if try_budget_alternatives and (proposal := _higher_budget(revised)):
            adjusted, budget_change = proposal
            adjusted['request_revision'] = revised['request_revision']
            yield adjusted, {'parts': [change, budget_change]}, '同时试排两天行程和增加后的预算', True
    if code != 'no_eligible_intercity_transport' or not snapshot:
        return
    missing = failure.get('missing_directions', detail.get('missing_directions', []))
    rules = [i for i in spec['items'] if i['field'] == 'intercity_mode' and i['strength'] == 'hard']
    if len(missing) != 1 or len(rules) != 1:
        return
    direction, = missing
    item, = rules
    value = item['value']
    mode = value[0] if isinstance(value, list) and len(value) == 1 else value
    groups = snapshot['intercity'].get(direction, [])
    if (item['scope'] not in TICKET_SCOPE or item['scope'] in {'outbound', 'return'} - {direction}
            or item['operator'] not in ('select', 'eq', 'allowed_set', 'any_of')
            or mode not in ('飞机', '火车') or not groups or any(g['errors'] for g in groups)):
        return
    revised, change = _changed(spec, item, '火车' if mode == '飞机' else '飞机',
                               '去程交通' if direction == 'outbound' else '返程交通', direction)
    yield revised, change, f"改用{change['after']}并核算整趟费用和衔接", False


def _city_draft(spec, snapshot, facts, deadline):
    items = {i['field']: i['value'] for i in spec['items']}
    city = items.get('destination_city')
    if not city or (snapshot is None and perf_counter() >= deadline):
        return None
    modes = set(DEFAULT_LOCAL_MODES)
    for rule in (i for i in spec['items'] if i['field'] == 'local_mode' and i['strength'] == 'hard'):
        modes.intersection_update(local_mode_options(rule) or ())
    candidates = (snapshot or facts.snapshot(city, limit=20))['candidates']['attraction']
    usable = [p for p in candidates if p['routable_in_snapshot'] and p['opening']['windows_minutes']]
    if not usable:
        return None

    def visit(place, arrived):
        stay = place['recommended_stay_hours']
        duration = max(60, round(stay[0] * 60)) if stay else 60
        for lo, hi in place['opening']['windows_minutes']:
            start = max(arrived, lo)
            if start + duration <= min(hi, 1200):
                return {'id': place['id'], 'name': place['name'], 'source': place['source'],
                        'reference_opening': place['opening']['windows_minutes'], 'stay_minutes': duration}, start + duration
        return None

    first = next(((p, result) for p in usable[:8] if (result := visit(p, 540))), None)
    if not first:
        return None
    place, (record, departure) = first
    result = {'city': city, 'stops': [record], 'segment': None,
              'condition': f'抵达{city}市区并留出白天游览时间时可选。出发前核对当天开放情况。',
              'pending': ['往返交通与衔接', '住宿与餐食', '整趟费用', '原需求中的指定预约和其他活动']}
    lat, lon = place['location']['lat'], place['location']['lon']
    nearby = sorted((p for p in usable if p['id'] != place['id']),
                    key=lambda p: (p['location']['lat'] - lat) ** 2 + (p['location']['lon'] - lon) ** 2)
    for other in nearby[:4]:
        for mode in DEFAULT_LOCAL_MODES:
            if perf_counter() >= deadline:
                return result
            if mode not in modes:
                continue
            route = facts.route(city, place['id'], other['id'], mode, clock(departure), people=items.get('people', 1))
            duration = route.get('duration_minutes')
            if (route['status'] != 'estimated' or duration is None or not 1 <= duration <= 45
                    or any(leg['mode'] not in modes for leg in route['legs'])):
                continue
            if stop := visit(other, departure + duration):
                result['stops'].append(stop[0])
                result['segment'] = {'from': place['name'], 'to': other['name'], 'mode': mode,
                                     'duration_minutes': duration, 'sources': route['provenance']}
                return result
    return result


def run_trip_with_recovery(spec, folder, env, facts, *, model=False, seconds=24, candidates=6,
                           recovery_seconds=RECOVERY_SECONDS, try_budget_alternatives=False, quality_seconds=0,
                           search_deadline=None, model_after_search=False):
    spec = normalize_saved_spec(spec)
    path = folder / 'RECOVERY.json'
    summary = {'state': 'starting', 'attempts': [], 'draft': None, 'restored': None,
               'model_calls': 0}
    if search_deadline is not None:
        summary.update(candidate_limit=candidates, remaining_seconds_at_start=round(max(0, search_deadline-perf_counter()), 2))
    save(path, summary)
    failure = run_trip_plan(spec, folder, env, facts, model=model, seconds=seconds, candidates=candidates,
                            quality_seconds=quality_seconds, search_deadline=search_deadline,
                            model_after_search=model_after_search)
    started = perf_counter()
    if failure['state'] == 'complete':
        save(path, {**summary, 'state': 'not_needed'})
        return failure
    deadline = search_deadline if search_deadline is not None else started + recovery_seconds + quality_seconds
    summary.update(state='running', cause={'code': failure.get('error_code'), 'message': failure['message'],
                                          'issues': failure.get('issues', [])})
    save(path, summary)
    snapshot_path = folder / 'WORLD_FACTS.json'
    snapshot = read(snapshot_path) if snapshot_path.is_file() else None
    proposals = list(_proposals(spec, failure, snapshot, try_budget_alternatives))[:len(ATTEMPT_IDS)]
    free = candidates
    for index, (revised, change, action, retry) in enumerate(proposals):
        if change and 'parts' in change and summary['attempts'] and summary['attempts'][0].get('view'):
            break
        now = perf_counter()
        # Reserve a small part of the overall budget for a destination draft.
        budget = (deadline - now - 1.5) / (len(proposals) - index)
        if budget <= 0 or free <= 0:
            break
        attempt_id = ATTEMPT_IDS[index]
        attempt = {'id': attempt_id, 'action': action, 'change': change, 'state': 'running'}
        summary['attempts'].append(attempt)
        save(path, summary)
        out = folder / attempt_id
        fuller = any(item['field']=='pace' and item['strength']=='soft' and item['value']=='充实'
                     for item in revised['items'])
        count = ceil(free / (len(proposals) - index)) if search_deadline is not None else min(candidates, 4 if fuller else 2)
        result = run_trip_plan(revised, out, env, facts, seconds=min(seconds, budget),
                               candidates=count,
                               improve_meals=False, saved_snapshot=snapshot if change is None else None,
                               finish_by=now + budget, recovery_retry=retry, quality_seconds=quality_seconds,
                               search_deadline=search_deadline)
        attempt.update(state=result['state'], message=result['message'], elapsed_seconds=perf_counter() - now)
        if result['state'] == 'complete':
            if search_deadline is not None:
                free -= result['candidate_count']
            if change is None:
                summary['restored'] = attempt_id
            else:
                attempt['view'] = read(out / 'PLAN_VIEW.json')
                attempt['snapshot_id'] = read(out / 'WORLD_FACTS.json')['snapshot_id']
                budget = next((p for p in change.get('parts', [change]) if p['field'] == 'budget'), None)
                if budget:
                    per_person = budget['label'].startswith('人均')
                    people = next(i['value'] for i in revised['items'] if i['field'] == 'people')
                    original_limit = budget['before'] * (people if per_person else 1)
                    cost = attempt['view']['candidates'][0]['metrics']['total_cost_cny']
                    attempt['cost_above_original_cny'] = round(max(0, cost - original_limit), 2)
        save(path, summary)
        if summary['restored']:
            break
    if not summary['restored'] and not any(a.get('view') for a in summary['attempts']):
        try:
            summary['draft'] = _city_draft(spec, snapshot, facts, deadline)
        except (KeyError, ValueError, RuntimeError):
            # A destination lookup may be out of scope or temporarily unavailable.
            summary['draft'] = None
    summary.update(state='complete', elapsed_seconds=perf_counter() - started)
    save(path, summary)
    return failure
