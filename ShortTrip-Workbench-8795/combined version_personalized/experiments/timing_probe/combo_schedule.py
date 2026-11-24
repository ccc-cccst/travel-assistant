"""Resumable root-combination trials around the existing daily scheduler."""
import copy
import heapq
import math
import time

from .day_sets import VISIT_STATE, DaySearchComplete, daily_agent, minute, required_names


def precheck(query, root):
    """Reject only chronology/appointment contradictions with known dates."""
    from experiments.c_only.rules import ticket_interval
    def interval(row, day):
        return ticket_interval({'start_time': row['BeginTime'], 'end_time': row['EndTime'],
                                'duration_hours': row.get('Duration')}, day)
    try:
        arrival = interval(root['go_transport'], 1)['end']
        departure = interval(root['back_transport'], query['days'])['start']
    except (KeyError, ValueError, TypeError, OverflowError):
        return None  # uncertain dates cannot prove impossibility
    if arrival >= departure:
        return {'rule': 'arrival_after_return', 'arrival': arrival, 'departure': departure}
    for item in query.get('appointments', ()):
        start = (item['day'] - 1) * 1440 + minute(item['start_time'])
        end = start + item.get('duration_minutes', 90)
        if arrival > start or departure < end:
            return {'rule': 'appointment_outside_trip', 'name': item['name']}
    return None


def dispatch(roots, make_trials, check_deadline, emit):
    """Reserve every fourth turn for a fresh root; resume others fairly."""
    waiting, opened = [], []
    source_done = False
    turns = 0
    ordinal = 0
    try:
        while waiting or not source_done:
            check_deadline()
            if not source_done and (not waiting or turns % 4 == 0):
                root = next(roots, None)
                if root is None:
                    source_done = True
                    continue
                trials = make_trials(root)
                opened.append(trials)
                task = (0, 0, ordinal, root, trials, 0, 0)
                ordinal += 1
            else:
                task = heapq.heappop(waiting)
            _, count, identity, root, trials, progress, stale = task
            check_deadline()
            emit({'event': 'dispatch', 'combination': identity, 'trial': count + 1})
            try:
                feedback = next(trials)
            except StopIteration:
                emit({'event': 'exhausted', 'combination': identity, 'trials': count})
            else:
                emit({'event': 'yield', 'combination': identity, **feedback})
                achieved = max(progress, feedback.get('progress', 0))
                stale = 0 if achieved > progress else stale + 1
                # Progress gets a bounded preference; repeated failure ages it out.
                priority = -achieved + stale / 3
                heapq.heappush(waiting, (priority, count + 1, identity, root, trials, achieved, stale))
            turns += 1
    finally:
        for trials in opened:
            trials.close()


def combination_agent(original, on_instance):
    """The historical two agents are not mutated by constructing this adapter."""
    base = daily_agent(original, lambda _agent: None)

    class CombinationAgent(base):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault('candidate_limit', 10)
            super().__init__(*args, **kwargs)
            self.combination_events = []
            self.combo_trial_seconds = 8.0
            self._trial_deadline = None
            model = self.backbone_llm
            original_send = model._send_request

            def bounded_send(messages, request_args):
                return original_send(messages, self.limit_model_request(request_args))

            model._send_request = bounded_send
            on_instance(self)

        def limit_model_request(self, request_args):
            args = dict(request_args)
            if self._trial_deadline is not None:
                # Reserve time for deterministic scheduling after model timeout.
                remaining = max(0.01, self._trial_deadline - time.monotonic() - 1.5)
                args['timeout'] = min(args.get('timeout', remaining), remaining)
                args['max_tokens'] = min(args.get('max_tokens', 4096), 512)
            return args

        def _prefix_rank(self, prefix):
            seen = {a.get('position') for d in prefix['plan'] for a in d['activities']}
            pending = required_names(self.query, 'attraction_names') | required_names(self.query, 'restaurant_names')
            return len(pending - seen), -len(prefix['plan'])

        def build_day_options(self, day, start_time, position, root, *, breakfast_done=False):
            # These safeguards belong only to v3. Keep the v2 implementation frozen.
            from openai import APITimeoutError
            finish = (minute(str(root['back_transport']['BeginTime'])) -
                      (60 if isinstance(root['back_transport'].get('TrainID'), str) else 120)
                      if day == self.query['days'] - 1 else self.timing_policy.attraction_end)
            begins = max(minute(start_time), self.timing_policy.attraction_start)
            visited = set(self.attraction_names_visiting)
            today = {a['name'] for a in self._appointments() if a['day'] == day + 1}
            required = today | ((required_names(self.query, 'attraction_names') - visited)
                                if day == self.query['days'] - 1 else set())
            if finish <= begins:
                self._day_preferred_restaurants = ()
                self.combination_events.append({'event': 'no_activity_window', 'day': day + 1,
                                                'start': begins, 'end': finish, 'required': sorted(required)})
                return [] if required else [()]
            try:
                result = super().build_day_options(day, start_time, position, root, breakfast_done=breakfast_done)
            except APITimeoutError:
                # A slow model is not evidence that this root cannot be completed.
                rows = self.memory['attractions']
                ids = {i for i in range(len(rows)) if rows.iloc[i]['name'] in required}
                others = sorted((i for i in range(len(rows)) if rows.iloc[i]['name'] not in visited),
                                key=lambda i: self._distance(position, rows.iloc[i]['name']))[:6]
                result = [tuple(sorted(ids | {i})) for i in others]
                self._day_preferred_restaurants = ()
                self.combination_events.append({'event': 'model_timeout_fallback', 'day': day + 1})
            # Always retain a required-only/return-only branch even after truncation.
            rows = self.memory['attractions']
            minimum = tuple(i for i in range(len(rows)) if rows.iloc[i]['name'] in required)
            if minimum and len(minimum) == len(required) and minimum not in result:
                result = [minimum, *result[:self.day_set_count - 1]]
            return result

        def _check_day_budget(self):
            super()._check_day_budget()
            if self._trial_deadline is not None and time.monotonic() >= self._trial_deadline:
                self._set_status = 'combination_wall_slice'
                raise DaySearchComplete()

        def _activate(self, task):
            self.required_rooms = task['rooms']
            self.intercity_with_hotel_cost = task['fixed_cost']

        def _trials(self, task):
            inner = self._iter_days(self.query, task['root'])
            try:
                while True:
                    self._activate(task)
                    self._trial_deadline = time.monotonic() + self.combo_trial_seconds
                    try:
                        feedback = next(inner)
                    except StopIteration:
                        return
                    finally:
                        self._trial_deadline = None
                        self._inside_day = False
                    yield feedback
            finally:
                inner.close()

        def _iter_days(self, query, root):
            """Continue a viable day prefix immediately, retaining alternatives."""
            empty = {name: [] for name in VISIT_STATE}
            self._restore_visits(empty)
            day, clock = self.outbound_next(root['go_transport'])
            plan = [{'day': i + 1, 'activities': []} for i in range(day + 1)]
            self.add_intercity_transport(plan[0]['activities'], root['go_transport'],
                                        innercity_transports=[], tickets=query['people_number'])
            initial = {'plan': plan, 'time': clock, 'position': root['go_transport']['To'], 'visits': empty}
            kept = {}

            def walk(prefix, current_day):
                self._restore_visits(prefix['visits'])
                start, breakfast_done = prefix['time'], False
                if start == '00:00' and current_day > 0:
                    tomorrow = copy.deepcopy(prefix['plan'])
                    tomorrow.append({'day': current_day + 1, 'activities': []})
                    start = self.day_resume_time(tomorrow, root, current_day, start)
                    breakfast = current_day * 1440 + 480
                    if any(o['day'] == current_day + 1 and o['kind'] == 'breakfast'
                           and o['status'] == 'required'
                           and any(lo <= breakfast and breakfast + 30 <= hi for lo, hi in o['available'])
                           for o in self.requirements(root)):
                        start, breakfast_done = '08:30', True
                options = self.build_day_options(current_day, start, prefix['position'], root,
                                                 breakfast_done=breakfast_done)
                preferred = self._day_preferred_restaurants
                for selected in options:
                    self._deadline_check()
                    self._restore_visits(prefix['visits'])
                    self._day_active, self._day_set = current_day, selected
                    self._day_restaurants = self._restaurant_pool(selected, prefix['position'], preferred)
                    self._meal_feasibility_cache = {}
                    self._day_results, self._set_candidates = [], 0
                    self._set_queries, self._set_misses = 0, 0
                    self._set_cpu = time.process_time()
                    started = time.monotonic()
                    self._inside_day, self._set_status = True, 'exhausted'
                    try:
                        self.dfs_poi(query, root, copy.deepcopy(prefix['plan']), prefix['time'],
                                     prefix['position'], current_day)
                    except DaySearchComplete:
                        pass
                    finally:
                        self._inside_day = False
                    successors = list(self._day_results)
                    feedback = {'progress': current_day + bool(successors),
                                'day': current_day + 1, 'schedules': len(successors),
                                'status': self._set_status, 'route_queries': self._set_queries,
                                'cpu_seconds': time.process_time() - self._set_cpu,
                                'seconds': time.monotonic() - started}
                    self.day_events.append({'event': 'set_trial', 'attractions': list(selected), **feedback})
                    yield feedback
                    if current_day + 1 < query['days']:
                        for successor in successors:
                            # Different meals/time/budget states may change future legality.
                            identity = successor['signature']
                            seen = kept.setdefault(current_day, set())
                            if identity in seen or len(seen) >= self.day_beam_width:
                                continue
                            seen.add(identity)
                            yield from walk(successor, current_day + 1)
            try:
                yield from walk(initial, day)
            finally:
                self._inside_day, self._day_active = False, None
                self._restore_visits(empty)

        def _root_tasks(self, query, go_rows, back_rows, go_rank, hotel_rank, room_count, room_type):
            for go_i in go_rank:
                self._deadline_check()
                go = go_rows.iloc[go_i]
                proposed = self.ranking_intercity_transport_back(back_rows, query, go)
                returns = self.reranking_intercity_transport_back_with_constraints(proposed, back_rows, query, go)
                hotels = hotel_rank if query['days'] > 1 else [None]
                # Diagonals expose different return/hotel choices without dropping any.
                for diagonal in range(len(returns) + len(hotels) - 1):
                    for back_index in range(max(0, diagonal - len(hotels) + 1), min(len(returns), diagonal + 1)):
                        self._deadline_check()
                        back_i, hotel_i = returns[back_index], hotels[diagonal - back_index]
                        root = {'go_transport': go, 'back_transport': back_rows.iloc[back_i]}
                        rooms = None
                        if hotel_i is not None:
                            hotel = self.memory['accommodations'].iloc[hotel_i]
                            beds = hotel['numbed']
                            if not math.isfinite(float(beds)) or beds <= 0:
                                continue
                            if room_type is not None and room_type != beds:
                                continue
                            rooms = room_count if room_count is not None else math.ceil(query['people_number'] / beds)
                            if room_type is None and rooms * beds < query['people_number']:
                                continue
                            root['accommodation'] = hotel
                        fixed = float(go['Cost'] + root['back_transport']['Cost']) * query['people_number']
                        if hotel_i is not None:
                            fixed += float(hotel['price']) * rooms * (query['days'] - 1)
                        task = {'root': root, 'rooms': rooms, 'fixed_cost': fixed}
                        self._activate(task)
                        conflict = precheck(query, root)
                        summary = {'go': str(go.get('TrainID', go.get('FlightID'))),
                                   'return': str(root['back_transport']['BeginTime']),
                                   'hotel': str(root.get('accommodation', {}).get('name', 'none'))}
                        if conflict or self.prune_partial(query, [], root):
                            self.combination_events.append({'event': 'precheck_reject', **summary,
                                                            'reason': conflict or 'existing_partial_rules'})
                            continue
                        self.combination_events.append({'event': 'admit', **summary, 'fixed_cost': fixed})
                        yield task

        def generate_plan_with_search(self, query):
            import pandas as pd
            from chinatravel.agent.nesy_agent.pruning import local_ranking
            source, destination = query['start_city'], query['target_city']
            go_rows = pd.concat([self.collect_intercity_transport(source, destination, kind)
                                 for kind in ('train', 'airplane')], ignore_index=True)
            back_rows = pd.concat([self.collect_intercity_transport(destination, source, kind)
                                   for kind in ('train', 'airplane')], ignore_index=True)
            self.time_before_search, self.llm_inference_time_count = time.time(), 0
            self.prune_counts, self.prune_examples = {}, {}
            self.required_rooms = None
            self._restore_visits({name: [] for name in VISIT_STATE})
            self.ranking_attractions_flag = self.ranking_restaurants_flag = False
            for name in ('llm_rec_format_error', 'llm_rec_count', 'search_nodes', 'backtrack_count',
                         'constraints_validation_count', 'commonsense_pass_count', 'logical_pass_count', 'all_constraints_pass'):
                setattr(self, name, 0)
            self.least_plan_schema = self.least_plan_comm = self.least_plan_logic = None
            self.least_plan_logical_pass = -1
            if go_rows.empty or back_rows.empty:
                return False, {'error_info': 'No priced intercity transport.'}
            ranking = self.ranking_intercity_transport_go(go_rows, query)
            go_rank = self.reranking_intercity_transport_go_with_constraints(ranking, go_rows, query)
            room_count, room_type = self.decide_rooms(query) if query['days'] > 1 else (None, None)
            self.required_budget = self.extract_budget(query)
            hotel_rank = []
            if query['days'] > 1:
                ranking = self.ranking_hotel(self.memory['accommodations'], query)
                hotel_rank = self.reranking_hotel_with_constraints(ranking, self.memory['accommodations'], query, room_count)
            self.innercity_transports_ranking_from_query = local_ranking(query, self.ranking_innercity_transport_from_query(query))
            if not self.innercity_transports_ranking_from_query:
                return False, {'error_info': 'No permitted local transport mode.'}
            roots = self._root_tasks(query, go_rows, back_rows, go_rank, hotel_rank, room_count, room_type)
            dispatch(roots, self._trials, self._deadline_check, self.combination_events.append)
            return False, {'error_info': 'Bounded combination trials exhausted; not proof of infeasibility.'}

    return CombinationAgent
