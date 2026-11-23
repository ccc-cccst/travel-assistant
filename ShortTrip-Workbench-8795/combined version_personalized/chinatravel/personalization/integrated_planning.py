"""Shared planning entry for the confirmed TripSpec page and offline replays."""

from collections import Counter
from time import perf_counter

from .city_scope import frozen_city_outcome
from .cli import save, read
from .lessons import describe_exception
from .meal_refinement import refine_meals
from .open_requirements import requirement_report
from .plan_assessment import evaluate_archive, select_candidates
from .planning_compat import check_snapshot_reference, constructor_input
from .sketch_planning import additional_layouts, day_transport_options, optimize_day, plan_from_sketches
from .trip_sketch import propose_trip_sketches
from .tripspec import normalize_saved_spec
from .world_facts import clock_minutes
from .visit_refinement import refine_visits


INTERCITY = {'train', 'airplane'}
SEARCH_DETAIL = {'fixed_budget': '试排的多数组合在车票和住宿阶段已超出金额预算。',
                 'actual_budget': '试排组合加上游览、餐食和市内交通后超过整团预算。',
                 'scoped_budget': '试排组合在用户指定的费用范围内超过预算。',
                 'arrival_return_window': '试排的班次留给市内活动和返程进站的时间不足。',
                 'terminal_access': '试排的组合多次无法在可用时间内接上车站、机场或酒店。',
                 'meal_access': '试排多次未能安排用餐及其前后接驳。',
                 'no_compatible_hotel': '本地可用于试排的住宿均未通过当前住宿条件。',
                 'no_attractions': '试排的组合没有安排下景点游览。'}


def same_day_conflict(snapshot):
    if snapshot['days'] != 1:
        return None
    clocks = {}
    for direction in ('outbound', 'return'):
        groups = snapshot['intercity'].get(direction, [])
        times = []
        for group in groups:
            if group['errors'] or len(group['records']) != group['counts']['snapshot_records']:
                return None
            for row in group['records']:
                begin, end = clock_minutes(row['departure']), clock_minutes(row['arrival'])
                duration = row['duration_minutes']
                if begin is None or end is None or duration is None or duration <= 0:
                    return None
                # V20 includes services whose duration differs from the clocks by two minutes.
                # Accept small rounding differences only; a whole extra day is an overnight service.
                elapsed = end - begin
                extra_days = round((duration - elapsed) / 1440)
                if extra_days < 0 or abs(elapsed + extra_days * 1440 - duration) > 5:
                    return None
                if end == begin and extra_days == 0:
                    return None
                if end > begin and extra_days == 0:
                    times.append((end, row['arrival']) if direction == 'outbound' else (begin, row['departure']))
        if not times:
            return None
        clocks[direction] = min(times) if direction == 'outbound' else max(times)
    if clocks['outbound'][0] >= clocks['return'][0]:
        return {'earliest_arrival': clocks['outbound'][1], 'latest_departure': clocks['return'][1]}
    return None


def display_days(plan, spec, *, ordered=False):
    locks = {(a['value']['day'], a['value']['name'], a['value']['start_time']): a['value']['duration_minutes']
             for a in spec['items'] if a['field'] == 'appointment'}
    days = []
    explicit_rooms = any(a['field'] == 'room_count' and a['strength'] == 'hard' for a in spec['items'])
    for day in plan['itinerary']:
        visits = []
        for a in day['activities']:
            if a['type'] in INTERCITY:
                info = {'name': f"{a['start']} → {a['end']}", 'kind': a['type'],
                        'service': a.get('TrainID', a.get('FlightID')),
                        'time': f"{a['start_time']}–{a['end_time']}"}
            else:
                info = {'name': a['position'], 'kind': a['type']}
                if a['type'] == 'accommodation':
                    info.update(rooms=a['rooms'], estimated_rooms=not explicit_rooms)
                if (day['day'], a['position'], a['start_time']) in locks:
                    info['time'] = f"{a['start_time']}–{a['end_time']}"
                    info['locked'] = True
            if ordered:
                info['incoming_minutes'] = sum(clock_minutes(t['end_time']) - clock_minutes(t['start_time'])
                                               for t in a['transports'])
                info['incoming_modes'] = list(dict.fromkeys(t['mode'] for t in a['transports']))
            visits.append(info)
        if not ordered:
            visits.sort(key=lambda p: (p['kind'] not in INTERCITY, p['kind'] == 'accommodation', p['kind'], p['name']))
        days.append({'day': day['day'], 'activities': visits})
    return days


def _card(raw, assessed, spec, facts=None):
    q = assessed['quality_vector']
    goals = assessed['goals']
    pending_core = [g for g in goals if g.get('primary') and g['status'] != 'matched']
    plan = raw['itinerary']
    days = display_days(plan, spec, ordered=True)
    for day, actual, measured in zip(days, plan['itinerary'], q['days']):
        day.update(ordered=True, visit_minutes=measured['visit_minutes'],
                   transit_minutes=measured['city_transit_minutes'],
                   group_cost_cny=round(sum(a['cost'] + sum(t['cost'] for t in a['transports'])
                                            for a in actual['activities']), 2))
        for item, activity in zip(day['activities'], actual['activities']):
            item['group_cost_cny'] = activity['cost']
            if item['kind'] in INTERCITY:
                continue
            if item['kind'] != 'accommodation':
                item['stay_minutes'] = clock_minutes(activity['end_time']) - clock_minutes(activity['start_time'])
            for goal in goals:
                if goal.get('preferred_start') and any(
                        p['day']==day['day'] and p['name']==item['name'] for p in goal['matched_places']):
                    item['requested_period'] = '傍晚游览' if goal['preferred_start']=='17:00' else '晚间游览'
            if facts:
                kind = 'restaurant' if item['kind'] in ('dinner','lunch','breakfast') else item['kind']
                found = facts.place(plan['target_city'], item['name'], kind, dining_details=True)
                if found['status'] == 'found':
                    record = found['records'][0]
                    item.update(tags=record['tags']['values'], location=record['location'])
                    if kind=='restaurant':
                        item['reference_dishes'] = record.get('reference_dishes', [])
                    if kind=='attraction':
                        item['reference_stay_hours'] = record['recommended_stay_hours']
                    if item['kind'] != 'accommodation':
                        item['opening_windows'] = record['opening']['windows_minutes']
    return {'id': raw['id'], 'source': raw['source'], 'days': days, 'city': plan['target_city'], 'guide_version': 1,
            'metrics': {k: q[k] for k in ('total_cost_cny', 'city_transit_minutes', 'access_walk_minutes',
                                           'visit_minutes', 'cost_breakdown_cny', 'longest_transfer_minutes')},
            'goals': [{**{k: g[k] for k in ('field', 'operator', 'value', 'status', 'reason')},
                       'primary': g.get('primary', False),
                       'preferred_start': g.get('preferred_start'),
                       'full_visit_days': g.get('observed') if g.get('metric') == 'full_visit_days' else None,
                       'matched_count': g.get('observed') if (
                           g.get('maximize_matches') or g.get('primary')) else None} for g in goals],
            'delivery_status': 'core_experience_pending' if pending_core else 'complete',
            'room_notes': [i['source'].get('text') or i['value'] for i in spec['items'] if i['field'] == 'room_type'],
            'pending': assessed['pending_verifications'],
            'budget_checks': [check for check in assessed['validation_report']['hard_requirements']
                              if check['field'] == 'budget' and check['scope'] != 'total_trip']}


def _include_trial_candidate(archive, assessment, entry, assessed, limit):
    """Keep the original recommendation and first published plan in the archive."""
    if not assessed['validation_report']['passed']:
        return 'verification_failed'
    candidate_id = entry['id']
    if any(candidate['id'] == candidate_id for candidate in assessment['candidates']):
        return 'duplicate'
    initial = assessment['selection']['recommended_id']
    entries = [*archive['archive'], entry]
    checked = [*assessment['candidates'], assessed]
    eligible = [candidate for candidate in checked if candidate['validation_report']['passed']]
    selection = select_candidates(eligible, keep_recommended_id=initial)
    visible = {selection['recommended_id'], *(option['id'] for option in selection['alternatives'])}
    if candidate_id not in visible:
        return 'not_comparable'
    if len(entries) > limit:
        protected = visible | {initial, entries[0]['id']}
        removable = [candidate['id'] for candidate in entries if candidate['id'] not in protected]
        frontier = set(selection['frontier_ids'])
        for candidate in sorted(removable, key=frontier.__contains__):
            remaining = [item for item in eligible if item['id'] != candidate]
            ranked = select_candidates(remaining, keep_recommended_id=initial)
            if (ranked['recommended_id'] == selection['recommended_id']
                    and ranked['alternatives'] == selection['alternatives']):
                entries = [item for item in entries if item['id'] != candidate]
                checked = [item for item in checked if item['id'] != candidate]
                eligible, selection = remaining, ranked
                break
        else:
            return 'no_archive_slot'
    archive.update(archive=entries, initial_recommended_id=initial)
    assessment.update(candidates=checked, candidate_count=len(checked), verified_count=len(eligible), selection=selection)
    return 'added'


def run_trip_plan(spec, folder, env, facts, *, model=False, saved_layouts=None, seconds=24, candidates=6,
                  improve_meals=True, saved_snapshot=None, sketch_provider=None, finish_by=None,
                  recovery_retry=False, quality_seconds=0, search_deadline=None, targeted_trial=None,
                  model_after_search=False):
    """Run all four stages and publish a compact preview only after full verification."""
    started = perf_counter()
    if saved_snapshot is None:
        spec = normalize_saved_spec(spec)
    save(folder / 'request.json', spec)
    timings = {}
    identity = {'request_id': spec.get('request_id'), 'request_revision': spec.get('request_revision')}
    selected = spec.get('model_provider')
    if selected:
        identity['model_provider'] = selected
    if model and selected == 'qwen' and sketch_provider is None:
        from .local_model import request_local_sketch
        sketch_provider = request_local_sketch
    base_limit = min(6, candidates) if search_deadline is not None else candidates
    cutoff = min((d for d in (finish_by, search_deadline) if d is not None), default=float('inf'))
    late_trial = targeted_trial == 'visit_day_after_search'
    if search_deadline is not None:
        identity['candidate_limit'] = candidates

    def progress(stage):
        save(folder / 'STATUS.json', {'state': 'planning', 'stage': stage,
                                      'protocol': 'integrated_planning_v1', 'timings': timings, **identity})

    def finish(state, message, **extra):
        value = {'state': state, 'message': message, 'stage': stage, 'protocol': 'integrated_planning_v1',
                 'timings': timings, 'elapsed_seconds': perf_counter() - started, **identity, **extra}
        return save(folder / 'STATUS.json', value)

    def out_of_time():
        return perf_counter() >= cutoff

    def timed_out():
        return finish('search_timeout', '本次试排到达时限。', reason_code='search_timeout')

    stage = 'input'
    try:
        if not spec.get('ready'):
            save(folder / 'REQUIREMENT_REPORT.json', requirement_report(spec, {}, []))
            return finish('needs_confirmation', '请先确认需求中的待处理事项。', issues=spec.get('issues', []))
        request, unsupported = constructor_input(spec)
        frozen = frozen_city_outcome({'trip': {'origin_city': request['start_city'], 'destination_city': request['target_city']}})
        if frozen:
            return finish(frozen['state'], frozen['message'], reason_code=frozen['reason_code'], evidence=frozen['evidence'])
        if unsupported:
            save(folder / 'REQUIREMENT_REPORT.json', requirement_report(spec, request, unsupported))
            return finish('planning_constraint_unsupported', '当前规划器无法核验以下要求。',
                          evidence={'unsupported_requirements': unsupported})
        if request.get('local_modes') == ():
            return finish('local_mode_conflict', '市内交通条件互相冲突，没有一种可同时符合。请确认允许的交通方式。',
                          evidence={'conflicting_requirements': [i for i in spec['items']
                                    if i['field'] == 'local_mode' and i['strength'] == 'hard']})
        if 'lodging_nights' in request and request['lodging_nights'] != request['days'] - 1:
            return finish('needs_confirmation', '住宿晚数与当前往返行程的天数不匹配，请核对。',
                          evidence={'requested_nights': request['lodging_nights'], 'trip_days': request['days']})
        stage = 'world_facts'
        progress(stage)
        tick = perf_counter()
        snapshot = saved_snapshot if saved_snapshot is not None else facts.trip_snapshot(spec)
        check_snapshot_reference(spec, snapshot, facts)
        timings[stage] = perf_counter() - tick
        save(folder / 'WORLD_FACTS.json', snapshot)
        request, _ = constructor_input(spec, snapshot=snapshot)
        save(folder / 'REQUIREMENT_REPORT.json', requirement_report(spec, request, [], snapshot))
        if request.get('required_visit_queries'):
            missing=[i for i in snapshot['requirements'] if i['field']=='attraction' and
                     i['strength'] in ('hard','fact') and i['operator'] in ('select','eq','require','include')
                     and i['id'] not in request['required_visit_ids']]
            if missing:
                return finish('visit_evidence_unavailable','请确认具体景点或入口：'+'、'.join(i['value'] for i in missing),
                              evidence={'unresolved_visits':missing,'place_groundings':snapshot['place_groundings']})
        missing_exclusions = [i for i in snapshot['requirements'] if i['field'] == 'attraction'
                              and i['strength'] == 'hard' and i['operator'] == 'exclude'
                              and i['id'] not in request.get('excluded_visit_ids', {})]
        if missing_exclusions:
            return finish('visit_evidence_unavailable', '请确认要跳过的具体景点：'
                          + '、'.join(str(i['value']) for i in missing_exclusions),
                          evidence={'unresolved_visits': missing_exclusions})
        if request.get('required_hotel_queries') and len(request['required_hotel_ids']) != len(
                request['required_hotel_queries']):
            ids = {i['id'] for i in snapshot['requirements'] if i['field'] == 'hotel' and i['strength'] == 'hard'}
            pending = ids - request['required_hotel_ids'].keys()
            matches = [g for g in snapshot['place_groundings'] if g['requirement_id'] in pending]
            branches = list(dict.fromkeys(p['name'] for group in matches if group.get('modifiers')
                                          for p in group['records'] if p['planning_catalog']
                                          and p['routable_in_snapshot']))
            message = '本地资料无法唯一确认可用于试排的指定酒店。'
            if branches:
                message = '请确认要住的分店。可查到：' + '、'.join(branches[:4])
            return finish('lodging_evidence_unavailable', message,
                          evidence={'hotel_groundings': matches, 'unresolved_hotel_ids': sorted(pending)})
        missing = [direction for direction, groups in snapshot['intercity'].items()
                   if not any(g['counts']['snapshot_records'] for g in groups)]
        if missing:
            directions = '、'.join('去程' if d == 'outbound' else '返程' for d in missing)
            return finish('no_eligible_intercity_transport', f'本地旅行快照中没有符合要求的{directions}班次。可调整城际交通方式后重试。',
                          missing_directions=missing)
        clash = same_day_conflict(snapshot)
        if clash:
            return finish('no_verified_candidate',
                          f"本地快照中，当日去程最早 {clash['earliest_arrival']} 到达，返程最晚 "
                          f"{clash['latest_departure']} 出发。若可过夜，可延长行程后重试。",
                          reason_code='same_day_ticket_window', evidence=clash)

        stage = 'trip_sketch'
        if out_of_time():
            return timed_out()
        progress(stage)
        tick = perf_counter()
        provider = {'provider': sketch_provider} if sketch_provider is not None else {}
        layouts = (saved_layouts if saved_layouts is not None else
                   propose_trip_sketches(snapshot, use_model=model and not model_after_search,
                                         timeout=max(.01, min(24 if selected == 'qwen' else 12,
                                                             cutoff-perf_counter())),
                                         **provider))
        timings[stage] = perf_counter() - tick
        save(folder / 'TRIP_SKETCHES.json', layouts)
        deferred = layouts
        if model_after_search:
            layouts = {**layouts, 'sketches': [s for s in layouts['sketches'] if s['source'] != 'model']}
        model_result = layouts['model']
        model_summary = {'requests': model_result['attempts'] if saved_layouts is None else 0,
                         'source': 'saved_replay' if saved_layouts is not None else 'live' if model else 'program',
                         'error': model_result['error'], 'usage': model_result['usage'] if saved_layouts is None else {},
                         'name': model_result.get('name'),
                         'accepted_layouts': sum(s['source'] == 'model' for s in layouts['sketches'])}

        def preview(candidate):
            if out_of_time():
                return
            check = evaluate_archive(spec, snapshot, {
                'schema_version': 'sketch_trial_v1', 'snapshot_id': snapshot['snapshot_id'], 'archive': [candidate],
            }, env, facts)
            if check['verified_count'] == 1:
                save(folder / 'WORKBENCH_FIRST.json', {'snapshot_id': snapshot['snapshot_id'],
                     'candidate': candidate, 'assessment': check['candidates'][0]})
                save(folder / 'FIRST_VIEW.json', {'candidate': _card(candidate, check['candidates'][0], spec, facts),
                                                  'elapsed_seconds': perf_counter() - started})

        stage = 'search'
        if out_of_time():
            return timed_out()
        progress(stage)
        tick = perf_counter()
        trial_deadline = min(tick+seconds, cutoff)
        archive = plan_from_sketches(spec, snapshot, layouts, env, facts, max_seconds=seconds,
                                     max_candidates=base_limit, on_first=None if finish_by else preview,
                                     finish_by=trial_deadline - .5 if cutoff != float('inf') else None,
                                     recovery_retry=recovery_retry, targeted_trial=None if late_trial else targeted_trial)
        timings[stage] = perf_counter() - tick
        save(folder / 'CANDIDATE_ARCHIVE.json', archive)
        if not archive.get('archive'):
            code = 'search_exhausted' if archive['state'] == 'no_verified_candidate' else archive['state']
            rejection_counts = Counter(i['code'] for check in archive.get('checked', []) if not check['passed']
                                       for i in check['issues'])
            reasons = Counter()
            for direction in archive.get('diagnostics', []):
                reasons.update(direction['reasons'])
            message = archive['message']
            if code == 'search_timeout':
                message = '试排达到搜索时限，未找到通过核验的行程。'
            elif code == 'search_exhausted' and rejection_counts:
                message += '已生成的候选未通过独立核验。'
            elif code == 'search_exhausted' and reasons:
                dominant = reasons.most_common(1)[0][0]
                message += SEARCH_DETAIL.get(dominant, '')
            return finish(archive['state'], message, reason_code=code, model=model_summary,
                          stop_reason=archive.get('stop_reason'),
                          evidence={'search_diagnostics': archive.get('diagnostics', []),
                                    'issues': archive.get('issues', []),
                                    'verification_rejections': dict(rejection_counts)})

        stage = 'assessment'
        if out_of_time() and search_deadline is None:
            return timed_out()
        progress(stage)
        tick = perf_counter()
        assessment = evaluate_archive(spec, snapshot, archive, env, facts)
        timings[stage] = perf_counter() - tick
        meal_seconds = min(seconds - archive['search_seconds'], cutoff - perf_counter() - .5)
        if improve_meals and assessment['selection']['recommended_id'] and meal_seconds > 0:
            archive, assessment, dining = refine_meals(spec, snapshot, archive, assessment, env, facts,
                                                       seconds=meal_seconds, max_candidates=candidates)
            timings['meal_refinement'] = dining['seconds']
            save(folder / 'MEAL_REFINEMENT.json', dining)
            if dining['state'] == 'added_candidate':
                save(folder / 'CANDIDATE_ARCHIVE.json', archive)
        selected=next((a for a in assessment['candidates'] if a['id']==assessment['selection']['recommended_id']),None)
        remaining=min(trial_deadline, finish_by or trial_deadline)-perf_counter()
        region=layouts.get('regional_alternative')
        if (region and selected and not recovery_retry and remaining>0.5 and len(archive['archive'])<base_limit
                and selected['quality_vector']['city_transit_minutes']>selected['quality_vector']['visit_minutes']):
            started_region=perf_counter()
            trial=plan_from_sketches(spec,snapshot,{**layouts,'sketches':[region]},env,facts,
                                     max_seconds=remaining,max_candidates=1,finish_by=started_region+remaining,
                                     regional_only=True)
            additions=[a for a in trial['archive'] if all(a['id']!=b['id'] for b in archive['archive'])]
            if additions:
                archive['archive'].extend(additions)
                assessment=evaluate_archive(spec,snapshot,archive,env,facts)
            archive['regional_trial']={'diagnostics':trial['diagnostics'],'new_candidates':len(additions)}
            archive['route_calls']+=trial['route_calls']
            archive['search_seconds']+=trial['search_seconds']
            save(folder / 'CANDIDATE_ARCHIVE.json', archive)
            timings['regional_trial']=perf_counter()-started_region
        if out_of_time() and search_deadline is None:
            return timed_out()
        if (quality_seconds and assessment['selection']['recommended_id']
                and (search_deadline is None or len(archive['archive']) < candidates)):
            quality_budget = min(quality_seconds, cutoff - perf_counter() - 2)
            if quality_budget >= 1:
                stage = 'visit_refinement'
                progress(stage)
                began = perf_counter()
                try:
                    archive, assessment, visits = refine_visits(spec, snapshot, archive, assessment, env, facts, quality_budget)
                except (KeyError, TypeError, ValueError, RuntimeError) as error:
                    visits = {'state':'kept_verified_plan', 'diagnostic':describe_exception(error, stage),
                              'seconds':perf_counter()-began}
                timings[stage] = visits['seconds']
                save(folder / 'VISIT_REFINEMENT.json', visits)
                if visits['state'] == 'verified_variant':
                    save(folder / 'CANDIDATE_ARCHIVE.json', archive)
        if search_deadline is not None and assessment['selection']['recommended_id']:
            free = candidates - len(archive['archive'])
            remaining = cutoff - perf_counter() - 3
            if free > 0 and remaining > .5:
                stage = 'extended_search'
                progress(stage)
                began = perf_counter()
                try:
                    trial = plan_from_sketches(spec, snapshot, additional_layouts(layouts, snapshot), env, facts,
                                               max_seconds=remaining, max_candidates=free, finish_by=cutoff-3,
                                               previous_entries=archive['archive'], regional_only=True)
                    additions = trial['archive']
                    if additions:
                        extra = evaluate_archive(spec, snapshot, trial, env, facts)
                        checked = [*assessment['candidates'], *extra['candidates']]
                        eligible = [c for c in checked if c['validation_report']['passed']]
                        initial = assessment['selection']['recommended_id']
                        selection = select_candidates(eligible, initial_recommended_id=initial)
                        archive['initial_recommended_id'] = initial
                        assessment = {**assessment, 'candidates':checked, 'candidate_count':len(checked),
                                      'verified_count':len(eligible), 'selection':selection}
                        archive['archive'].extend(additions)
                    archive['route_calls'] += trial['route_calls']
                    archive['search_seconds'] += trial['search_seconds']
                    archive['stop_reason'] = trial['stop_reason']
                    archive['extended_search'] = {'stop_reason':trial['stop_reason'], 'added':len(additions),
                                                  'diagnostics':trial['diagnostics'], 'checked':trial['checked']}
                except (ValueError, KeyError, RuntimeError, TypeError) as error:
                    archive['extended_search'] = {'state':'kept_verified_plan', 'diagnostic':describe_exception(error, stage)}
                    archive['stop_reason'] = 'extended_search_failed'
                timings[stage] = perf_counter()-began
            elif not free:
                archive['stop_reason'] = 'candidate_limit'
            else:
                archive['stop_reason'] = 'deadline'
            save(folder / 'CANDIDATE_ARCHIVE.json', archive)
        if (late_trial and not recovery_retry and request['days'] == 3 and not layouts.get('fixed_appointments')
                and assessment['selection']['recommended_id'] and any(
                    item['source'].get('type') == 'map_selection' and item['source'].get('choice') == 'must'
                    for item in snapshot['requirements'])):
            began = perf_counter()
            trial_end = min(began + 3, cutoff - 3)
            if trial_end - began > .5:
                progress('targeted_trial')
                try:
                    trial = plan_from_sketches(
                        spec, snapshot, layouts, env, facts, max_seconds=3, max_candidates=1,
                        finish_by=trial_end, previous_entries=archive['archive'], targeted_only=True,
                        targeted_trial='visit_day_full_budget',
                    )
                    archive['route_calls'] += trial['route_calls']
                    archive['search_seconds'] += trial['search_seconds']
                    archive['diagnostics'].extend(trial['diagnostics'])
                    details = {'state': 'no_candidate', 'checked': trial['checked'],
                               'route_calls': trial['route_calls']}
                    if trial['archive']:
                        entry, = trial['archive']
                        extra = evaluate_archive(spec, snapshot, trial, env, facts)
                        assessed, = extra['candidates']
                        details.update(candidate_id=entry['id'], validation_report=assessed['validation_report'])
                        details['state'] = _include_trial_candidate(archive, assessment, entry, assessed, candidates)
                    archive['targeted_trial'] = details
                except (ValueError, KeyError, RuntimeError, TypeError) as error:
                    archive['targeted_trial'] = {'state': 'kept_verified_plan',
                                                 'diagnostic': describe_exception(error, 'targeted_trial')}
                timings['targeted_trial'] = perf_counter() - began
                save(folder / 'CANDIDATE_ARCHIVE.json', archive)
        if model_after_search and assessment['selection']['recommended_id'] and (model or saved_layouts is not None):
            began = perf_counter()
            stop = min(began + (3 if saved_layouts is not None else 7), cutoff - 3)
            details = {'state': 'insufficient_time'}
            if stop - began > (0.5 if saved_layouts is not None else 2):
                stage = 'model_alternative'
                progress(stage)
                try:
                    if saved_layouts is None:
                        deferred = propose_trip_sketches(snapshot, use_model=True,
                                                        timeout=min(4, stop - began - 1), **provider)
                        save(folder / 'TRIP_SKETCHES.json', deferred)
                        result = deferred['model']
                        model_summary.update(requests=result['attempts'], error=result['error'], usage=result['usage'],
                                             name=result.get('name'),
                                             accepted_layouts=sum(s['source'] == 'model' for s in deferred['sketches']))
                    sketches = [s for s in deferred['sketches'] if s['source'] == 'model']
                    remaining = min(3, stop - perf_counter())
                    details = {'state': 'no_model_layout', 'sketches': len(sketches)}
                    if sketches and remaining <= .5:
                        details['state'] = 'insufficient_time'
                    if sketches and remaining > .5:
                        trial = plan_from_sketches(spec, snapshot, {**deferred, 'sketches': sketches}, env, facts,
                                                   max_seconds=remaining, max_candidates=1, finish_by=stop,
                                                   previous_entries=archive['archive'], regional_only=True)
                        archive['route_calls'] += trial['route_calls']
                        archive['search_seconds'] += trial['search_seconds']
                        details.update(state='no_candidate', route_calls=trial['route_calls'],
                                       diagnostics=trial['diagnostics'], checked=trial['checked'])
                        if trial['archive']:
                            entry, = trial['archive']
                            assessed, = evaluate_archive(spec, snapshot, trial, env, facts)['candidates']
                            details.update(candidate_id=entry['id'], quality_vector=assessed['quality_vector'],
                                           goals=assessed['goals'], validation_report=assessed['validation_report'])
                            details['state'] = _include_trial_candidate(archive, assessment, entry, assessed, candidates)
                except (ValueError, KeyError, RuntimeError, TypeError) as error:
                    details = {'state': 'kept_verified_plan', 'diagnostic': describe_exception(error, stage)}
            archive['model_alternative'] = details
            model_summary['alternative_state'] = details['state']
            timings['model_alternative'] = perf_counter() - began
            save(folder / 'CANDIDATE_ARCHIVE.json', archive)
        save(folder / 'PLAN_ASSESSMENT.json', assessment)
        selection = assessment['selection']
        if not selection['recommended_id']:
            return finish('verification_failed', '候选复核未通过，请查看核验记录。', model=model_summary,
                          evidence={'candidate_count': assessment['candidate_count']})
        raw = {r['id']: r for r in archive['archive']}
        checked = {r['id']: r for r in assessment['candidates']}
        show = [selection['recommended_id'], *(a['id'] for a in selection['alternatives'])]
        save(folder / 'PLAN_VIEW.json', {'recommended_id': selection['recommended_id'],
                                         'alternatives': selection['alternatives'],
                                         'candidates': [_card(raw[i], checked[i], spec, facts) for i in show],
                                         'verified_count': assessment['verified_count']})
        report = requirement_report(spec, request, [], snapshot)
        recommended = checked[selection['recommended_id']]
        report['candidate_id'] = selection['recommended_id']
        report['hard_checks'] = recommended['validation_report']['hard_requirements']
        report['goals'] = recommended['goals']
        save(folder / 'REQUIREMENT_REPORT.json', report)
        early = folder / 'FIRST_VIEW.json'
        return finish('complete', '行程已根据旅行快照核验。', model=model_summary,
                      delivery_status='core_experience_pending' if any(g.get('primary') and g['status'] != 'matched'
                          for g in recommended['goals']) else 'complete',
                      stop_reason=archive['stop_reason'], candidate_count=assessment['verified_count'],
                      first_visible_seconds=read(early)['elapsed_seconds'] if early.exists() else perf_counter() - started)
    except Exception as error:
        failure = describe_exception(error, stage)
        return finish(failure['state'], failure['message'], reason_code=failure['reason_code'], evidence=failure['evidence'])


def run_trip_route(source, folder, candidate_id, day, env, facts, *, view_only=False):
    started = perf_counter()
    spec, snapshot = read(source / 'request.json'), read(source / 'WORLD_FACTS.json')
    save(folder / 'request.json', spec)
    save(folder / 'STATUS.json', {'state': 'planning', 'stage': 'route', 'protocol': 'integrated_planning_v1'})
    candidates = read(source / 'CANDIDATE_ARCHIVE.json')['archive']
    candidate = next(v for v in candidates if v['id'] == candidate_id)
    result = optimize_day(spec, snapshot, candidate['itinerary'], day, env, facts, view_only=view_only)
    if not result.get('itinerary'):
        return save(folder / 'STATUS.json', {'state': 'verification_failed', 'stage': 'route',
                                             'protocol': 'integrated_planning_v1', 'message': '行程未通过顺路前复核。',
                                             'issues': result.get('issues', [])})
    itinerary = result['itinerary']
    segments, option_calls = day_transport_options(spec, itinerary, day, env)
    save(folder / 'ROUTE_VIEW.json', {
        'candidate_id': candidate_id, 'day': day, 'result': result['state'], 'distance': result['distance'],
        'activities': display_days(itinerary, spec, ordered=True)[day - 1]['activities'], 'segments': segments,
        'minutes_saved': result['before']['city_transit_minutes'] - result['after']['city_transit_minutes'],
        'stop_reason': result['stop_reason'], 'rejections': result['rejections'],
        'route_queries': result['route_calls'] + option_calls, 'model_calls': result['model_calls'],
        'elapsed_seconds': perf_counter() - started,
    })
    return save(folder / 'STATUS.json', {'state': 'complete', 'stage': 'route', 'protocol': 'integrated_planning_v1'})
