"""Opt-in daily place-set generation and bounded scheduling for the C agent."""

import ast
import copy
import json
import math
import time


VISIT_STATE = (
    "attractions_visiting", "restaurants_visiting", "food_type_visiting",
    "spot_type_visiting", "attraction_names_visiting", "restaurant_names_visiting",
)


class DaySearchComplete(BaseException):
    """Yield after this set produces enough schedules or consumes its allowance."""


def minute(value):
    hour, part = map(int, value.split(":"))
    return hour * 60 + part


def day_capacity(start, finish, meal_minutes, required_count):
    available = max(0, finish - minute(start) - meal_minutes)
    return min(4, max(required_count, available // 90))


def read_proposals(text, allowed):
    """Discard malformed proposals, with order-independent identity and strict IDs."""
    try:
        value, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    except (ValueError, TypeError, AttributeError):
        return []
    proposals = value.get("sets") if isinstance(value, dict) else None
    if not isinstance(proposals, list):
        return []
    result = []
    for proposal in proposals:
        ids = proposal.get("attractions") if isinstance(proposal, dict) else None
        if not isinstance(ids, list) or any(type(i) is not int or i not in allowed for i in ids):
            continue
        identity = tuple(sorted(set(ids)))
        if identity not in result:
            result.append(identity)
    return result


def read_restaurants(text, allowed):
    try:
        value, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
        ids = value.get("restaurants", [])
    except (ValueError, TypeError, AttributeError):
        return ()
    if not isinstance(ids, list):
        return ()
    return tuple(dict.fromkeys(i for i in ids if type(i) is int and i in allowed))


def required_names(query, field):
    from chinatravel.agent.nesy_agent.pruning import facts
    names = set()
    for _, (symbol, op, value, shape) in facts(query):
        if symbol == field and shape == "set" and isinstance(op, (ast.LtE, ast.Eq)):
            names.update(value)
    return names


def appointment_slot(appointments, day, name, arrived, opens, closes, duration):
    """An explicit appointment is scheduled at its confirmed start or rejected."""
    matches = [a for a in appointments if a["day"] == day and a["name"] == name]
    if not matches:
        return None
    appointment = matches[0]
    begin = appointment["start_time"]
    length = appointment.get("duration_minutes", duration)
    finish = minute(begin) + length
    if minute(arrived) > minute(begin) or minute(opens) > minute(begin) or finish > minute(closes):
        return False
    return begin, f"{finish // 60:02d}:{finish % 60:02d}"


def geo_distance(left, right):
    if not isinstance(left, (tuple, list)) or not isinstance(right, (tuple, list)):
        return float("inf")
    try:
        lat1, lon1, lat2, lon2 = map(math.radians, (*left, *right))
        value = math.sin((lat2 - lat1) / 2) ** 2
        value += math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return 12742 * math.asin(math.sqrt(min(1, max(0, value))))
    except (ValueError, TypeError):
        return float("inf")


def daily_agent(original, on_instance):
    class DaySetAgent(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.day_events = []
            self.day_set_count = 6
            self.day_schedule_limit = 2
            self.day_beam_width = 6
            self.day_route_limit = 500
            self.day_cpu_limit = 1.5
            self._inside_day = False
            self._proposal_cache = {}
            self._day_routes = {}
            self._points = {}
            self._day_active = None
            on_instance(self)

        def _point(self, name):
            if name not in self._points:
                self._points[name] = self.env.poi.search(self.query["target_city"], name)
            return self._points[name]

        def _distance(self, first, second):
            return geo_distance(self._point(first), self._point(second))

        def _visits(self):
            return {name: list(getattr(self, name)) for name in VISIT_STATE}

        def _restore_visits(self, state):
            for name, values in state.items():
                setattr(self, name, list(values))

        def _set_done(self, plan):
            seen = {a.get("position") for a in plan[self._day_active]["activities"]
                    if a["type"] == "attraction"}
            return all(self.memory["attractions"].iloc[i]["name"] in seen for i in self._day_set)

        def _appointments(self):
            appointments = self.query.get("appointments", [])
            if not isinstance(appointments, list):
                raise ValueError("appointments must be a confirmed list")
            names = set(self.memory["attractions"]["name"])
            for item in appointments:
                if (not isinstance(item, dict) or item.get("name") not in names
                        or type(item.get("day")) is not int
                        or not 1 <= item["day"] <= self.query["days"]
                        or not isinstance(item.get("start_time"), str)):
                    raise ValueError("Appointment requires a known place, day and start_time")
                if not 0 <= minute(item["start_time"]) < 1440:
                    raise ValueError("Invalid appointment clock")
                duration = item.get("duration_minutes", 90)
                if type(duration) is not int or duration <= 0:
                    raise ValueError("Invalid appointment duration")
            if "预约" in self.query["nature_language"] and not appointments:
                raise ValueError("预约需先确认地点、日期与开始时间，并填写 appointments")
            return appointments

        def build_day_options(self, day, start_time, position, root, *, breakfast_done=False):
            """Batch semantic proposals, then recheck them against each prefix."""
            attractions = self.memory["attractions"]
            required = required_names(self.query, "attraction_names")
            visited = {attractions.iloc[i]["name"] for i in self.attractions_visiting}
            end = root["back_transport"]["From"] if day == self.query["days"] - 1 else root["accommodation"]["name"]
            appointments = self._appointments()
            today = {a["name"] for a in appointments if a["day"] == day + 1}
            other_days = {a["name"] for a in appointments if a["day"] != day + 1}
            types = required_names(self.query, "spot_type")

            def distance_key(i):
                name = attractions.iloc[i]["name"]
                distance = self._distance(position, name) + .35 * self._distance(name, end)
                type_mismatch = bool(types) and str(attractions.iloc[i]["type"]) not in types
                return name not in required | today, type_mismatch, distance, int(i)

            recalled = sorted(range(len(attractions)), key=distance_key)[:12]
            policy = getattr(self, 'personalization_policy', None)
            if policy is not None:
                ordered = sorted(range(len(attractions)), key=distance_key)
                pinned = {i for i in ordered if attractions.iloc[i]['name'] in required | today}
                recalled = policy.recall_day(attractions, ordered, pinned, visited, other_days, position, end, limit=12)
            cache_key = (day, start_time, position, end, str(root["go_transport"]["BeginTime"]),
                         str(root["back_transport"]["BeginTime"]), tuple(recalled),self.timing_policy.name,
                         policy.fingerprint if policy else None, tuple(sorted(visited)))
            if cache_key not in self._proposal_cache:
                cards = []
                for i in recalled:
                    row = attractions.iloc[i]
                    cards.append({"id": i, "name": row["name"], "type": str(row["type"]),
                                  "opens": row["opentime"], "closes": row["endtime"],
                                  "price": float(row["price"]),
                                  "start_km": round(self._distance(position, row["name"]), 1)})
                context = {"request": self.query["nature_language"], "day": day + 1,
                           "time_policy":self.timing_policy.name,
                           "meal_windows_minutes_from_midnight":self.timing_policy.meal_windows,
                           "days": self.query["days"], "start": position,
                           "available_from": start_time, "end": end,
                           "return_departure": str(root["back_transport"]["BeginTime"]),
                           "appointments": appointments, "attractions": cards}
                restaurants = self._restaurant_pool((), position, preferred=())
                context["restaurants"] = [
                    {"id": i, "name": self.memory["restaurants"].iloc[i]["name"],
                     "cuisine": str(self.memory["restaurants"].iloc[i]["cuisine"]),
                     "price": float(self.memory["restaurants"].iloc[i]["price"]),
                     "opens": self.memory["restaurants"].iloc[i]["opentime"],
                     "closes": self.memory["restaurants"].iloc[i]["endtime"]}
                    for i in restaurants]
                if policy is not None:
                    context['personalization'] = policy.context()
                    policy.enrich(cards, 'attraction')
                    policy.enrich(context['restaurants'], 'restaurant')
                prompt = (
                    "给这一天推荐最多六套互有差异的无序景点集合。考虑用户意图、地点搭配、"
                    "营业、游览所需时间、起终点接驳和用餐余量。首末日可能只容纳零到两个景点。"
                    "点名景点须在全程兑现，预约按指定日期。这里只选择地点，后续程序核验和排顺序。"
                    "同时从餐馆卡片中推荐符合用户口味的餐厅 ID，供午晚餐试排。"
                    "只输出短 JSON，格式 {\"sets\":[{\"attractions\":[1,2]},{\"attractions\":[3]}],\"restaurants\":[0,4]}。"
                    "每集合最多四个景点，id 必须来自输入，不输出分析或时间表。\n"
                    + json.dumps(context, ensure_ascii=False)
                )
                before = time.perf_counter()
                answer = self.backbone_llm([{"role": "user", "content": prompt}], one_line=False)
                elapsed = time.perf_counter() - before
                self.llm_inference_time_count += elapsed
                self.llm_rec_count += 1
                proposals = read_proposals(answer, set(recalled))
                preferred = read_restaurants(answer, set(restaurants))
                self._proposal_cache[cache_key] = proposals, preferred
                self.day_events.append({"event": "model_sets", "day": day + 1,
                                        "seconds": elapsed,
                                        "proposals": proposals, "format_fallback": not proposals,
                                        "restaurants": preferred,
                                        "context": context})

            remaining = [i for i in recalled if attractions.iloc[i]["name"] not in visited | other_days]
            must = today | ((required - visited) if day == self.query["days"] - 1 else set())
            must_ids = {i for i in range(len(attractions)) if attractions.iloc[i]["name"] in must}
            if {attractions.iloc[i]["name"] for i in must_ids} != must or must & visited:
                self.day_events.append({"event": "unavailable_required_place", "day": day + 1,
                                        "places": sorted(must)})
                return []
            buffer = 60 if str(root["back_transport"].get("TrainID", "nan")) != "nan" else 120
            finish = minute(root["back_transport"]["BeginTime"]) - buffer if day == self.query["days"] - 1 else self.timing_policy.attraction_end
            meals = sum(o["duration"] for o in self.requirements(root)
                        if o["day"] == day + 1 and o["status"] == "required"
                        and not (o["kind"] == "breakfast" and breakfast_done)
                        and o["window"][1] > day * 1440 + minute(start_time))
            capacity = day_capacity(start_time, finish, meals, len(must_ids))
            proposals = list(self._proposal_cache[cache_key][0])
            self._day_preferred_restaurants = self._proposal_cache[cache_key][1]
            # Include compact geographic neighborhoods around multiple seeds.
            for seed in remaining[:6]:
                near = sorted(remaining, key=lambda i: self._distance(
                    attractions.iloc[seed]["name"], attractions.iloc[i]["name"]))
                for size in (capacity, max(1, capacity - 1), 1):
                    proposals.append(tuple(set(near[:size]) | must_ids))
            proposals.append(tuple(must_ids))
            result = []
            for proposal in proposals:
                identity = tuple(sorted(set(proposal)))
                names = {attractions.iloc[i]["name"] for i in identity}
                if (len(identity) > capacity or not must_ids <= set(identity)
                        or names & (visited | other_days) or identity in result):
                    continue
                result.append(identity)
            return result[:self.day_set_count]

        def _restaurant_pool(self, selected, position, preferred=None):
            rows = self.memory["restaurants"]
            required = required_names(self.query, "restaurant_names")
            cuisine = required_names(self.query, "food_type")
            if preferred is None:
                preferred = self._day_preferred_restaurants
            anchors = [self.memory["attractions"].iloc[i]["name"] for i in selected] or [position]

            def key(i):
                row = rows.iloc[i]
                cuisine_mismatch = bool(cuisine) and str(row["cuisine"]) not in cuisine
                return (row["name"] not in required, cuisine_mismatch,
                        i not in preferred,
                        min(self._distance(row["name"], a) for a in anchors), float(row["price"]), i)

            ordered = sorted((i for i in range(len(rows)) if i not in self.restaurants_visiting), key=key)
            policy = getattr(self, 'personalization_policy', None)
            if policy is not None:
                pinned = {i for i in ordered if rows.iloc[i]['name'] in required}
                return policy.recall(rows, ordered, pinned, limit=8, kind='restaurant')
            return ordered[:8]

        def _prefix_rank(self, prefix):
            from chinatravel.soft_constraints.scoring import score_plan
            plan = {"target_city": self.query["target_city"], "itinerary": prefix["plan"]}
            seen = {a.get("position") for d in prefix["plan"] for a in d["activities"]}
            pending = required_names(self.query, "attraction_names") | required_names(self.query, "restaurant_names")
            score = score_plan(plan, config=self.soft_config)
            cost = sum(a["cost"] + sum(t["cost"] for t in a.get("transports", ()))
                       for d in prefix["plan"] for a in d["activities"])
            return len(pending - seen), score["total_penalty"], cost

        def _search_days(self, query, root):
            if self.prune_partial(query, [], root):
                return False, []
            day, start_time = self.outbound_next(root["go_transport"])
            plan = [{"day": i + 1, "activities": []} for i in range(day + 1)]
            self.add_intercity_transport(plan[0]["activities"], root["go_transport"],
                                        innercity_transports=[], tickets=query["people_number"])
            empty = {name: [] for name in VISIT_STATE}
            beam = [{"plan": plan, "time": start_time, "position": root["go_transport"]["To"], "visits": empty}]
            initial_visits = self._visits()
            try:
                for current_day in range(day, query["days"]):
                    next_beam = []
                    for prefix in beam:
                        self._restore_visits(prefix["visits"])
                        planning_start = prefix["time"]
                        breakfast_done = False
                        if planning_start == "00:00" and current_day > 0:
                            tomorrow = copy.deepcopy(prefix["plan"])
                            tomorrow.append({"day": current_day + 1, "activities": []})
                            planning_start = self.day_resume_time(tomorrow, root, current_day, planning_start)
                            breakfast = current_day * 1440 + 480
                            if any(o["day"] == current_day + 1 and o["kind"] == "breakfast"
                                   and o["status"] == "required"
                                   and any(lo <= breakfast and breakfast + 30 <= hi for lo, hi in o["available"])
                                   for o in self.requirements(root)):
                                planning_start = "08:30"
                                breakfast_done = True
                        options = self.build_day_options(current_day, planning_start, prefix["position"], root,
                                                         breakfast_done=breakfast_done)
                        for selected in options:
                            self._deadline_check()
                            self._restore_visits(prefix["visits"])
                            self._day_active = current_day
                            self._day_set = selected
                            self._day_restaurants = self._restaurant_pool(selected, prefix["position"])
                            self._meal_feasibility_cache = {}
                            self._day_results = []
                            self._set_candidates = 0
                            self._set_queries = 0
                            self._set_misses = 0
                            self._set_cpu = time.process_time()
                            prunes_before = dict(self.prune_counts)
                            started = time.perf_counter()
                            self._inside_day = True
                            status = "interrupted"
                            try:
                                self.dfs_poi(query, root, copy.deepcopy(prefix["plan"]),
                                             prefix["time"], prefix["position"], current_day)
                                status = "exhausted"
                            except DaySearchComplete:
                                status = self._set_status
                            finally:
                                self._inside_day = False
                                self.day_events.append({
                                    "event": "set_trial", "day": current_day + 1,
                                    "attractions": list(selected), "restaurants": self._day_restaurants,
                                    "seconds": time.perf_counter() - started,
                                    "cpu_seconds": time.process_time() - self._set_cpu,
                                    "route_queries": self._set_queries, "uncached_routes": self._set_misses,
                                    "schedules": len(self._day_results), "full_candidates": self._set_candidates,
                                    "prunes": {key: value - prunes_before.get(key, 0)
                                               for key, value in self.prune_counts.items()
                                               if value > prunes_before.get(key, 0)},
                                    "status": "candidate_limit" if self.pool.full else status})
                            next_beam.extend(self._day_results)
                    if current_day < query["days"] - 1:
                        diverse = {}
                        for prefix in sorted(next_beam, key=self._prefix_rank):
                            identity = tuple(tuple(sorted(a["position"] for a in d["activities"]
                                                         if a["type"] == "attraction")) for d in prefix["plan"])
                            if identity not in diverse:
                                diverse[identity] = prefix
                        beam = list(diverse.values())[:self.day_beam_width]
                        self.day_events.append({"event": "beam", "day": current_day + 1,
                                                "generated": len(next_beam), "kept": len(beam)})
                        if not beam:
                            break
            finally:
                self._inside_day = False
                self._day_active = None
                self._restore_visits(initial_visits)
            return False, []

        def _check_day_budget(self):
            self._deadline_check()
            if (self._set_misses >= self.day_route_limit
                    or time.process_time() - self._set_cpu >= self.day_cpu_limit):
                self._set_status = "trial_budget"
                raise DaySearchComplete()

        def dfs_poi(self, query, root, plan, current_time, current_position, current_day=0):
            if not self._inside_day:
                return self._search_days(query, root)
            self._check_day_budget()
            if current_day > self._day_active:
                if self._set_done(plan):
                    signature = json.dumps(plan, sort_keys=True, ensure_ascii=False, default=str)
                    if all(item["signature"] != signature for item in self._day_results):
                        self._day_results.append({"plan": copy.deepcopy(plan), "time": current_time,
                                                  "position": current_position, "visits": self._visits(),
                                                  "signature": signature})
                        if len(self._day_results) >= self.day_schedule_limit:
                            self._set_status = "schedule_limit"
                            raise DaySearchComplete()
                return False, plan
            return super().dfs_poi(query, root, plan, current_time, current_position, current_day)

        def collect_innercity_transport(self, city, start, end, start_time, trans_type):
            if self._inside_day:
                self._check_day_budget()
                self._set_queries += 1
                self._appointment_place = end
            key = city, start, end, start_time, trans_type
            if key not in self._day_routes:
                if self._inside_day:
                    self._set_misses += 1
                route = super().collect_innercity_transport(city, start, end, start_time, trans_type)
                self._day_routes[key] = copy.deepcopy(route)
            return copy.deepcopy(self._day_routes[key])

        def ranking_attractions(self, plan, root, day, clock, position, cost):
            return sorted(self._day_set, key=lambda i: self._distance(position, self.memory["attractions"].iloc[i]["name"]))

        def ranking_restaurants(self, plan, root, day, clock, position, cost):
            return sorted(self._day_restaurants, key=lambda i: self._distance(position, self.memory["restaurants"].iloc[i]["name"]))

        def reranking_attractions_with_constraints(self, plan, kind, day, clock, position, rows, query, indices):
            # Final DSL/C checks and partial pruning still validate complete plans.
            return indices

        def reranking_restaurants_with_constraints(self, plan, kind, day, clock, position, rows, query, indices):
            return indices

        def meals_possible_after(self, plan, root, day, end_time, position):
            rows = self.memory["restaurants"]
            self.memory["restaurants"] = rows.iloc[self._day_restaurants]
            try:
                return super().meals_possible_after(plan, root, day, end_time, position)
            finally:
                self.memory["restaurants"] = rows

        def close_day_allowed(self, plan, root, day):
            return self._set_done(plan) and super().close_day_allowed(plan, root, day)

        def select_next_poi_type(self, candidates, plan, root, day, clock, position):
            missing = self.missing_today(plan, root, day)
            required = {o["kind"] for o in missing}
            done = self._set_done(plan)
            candidates[:] = [kind for kind in candidates
                             if (kind not in {"breakfast", "lunch", "dinner"} or kind in required)
                             and (kind not in {"hotel", "back-intercity-transport"} or (done and not missing))
                             and (kind != "attraction" or not done)]
            if not candidates:
                return "attraction", candidates
            due = [o for o in missing if o["window"][0] <= day * 1440 + minute(clock)]
            due.sort(key=lambda o: o["window"][1])
            order = [o["kind"] for o in due] + ["attraction"] + [o["kind"] for o in missing]
            return next((kind for kind in order if kind in candidates), candidates[0]), candidates

        def attraction_slot(self, arrived, opens, closes, duration):
            # Existing DFS supplies the selected place through this scoped attribute.
            if getattr(self, "_appointment_place", None) is not None:
                slot = appointment_slot(self._appointments(), self._day_active + 1,
                                        self._appointment_place, arrived, opens, closes, duration)
                if slot is not None:
                    return slot or None
            return super().attraction_slot(arrived, opens, closes, duration)

        def select_poi_time(self, plan, root, day, clock, name, kind, recommended_visit_time):
            for item in self._appointments():
                if item["day"] == day + 1 and item["name"] == name:
                    return item.get("duration_minutes", 90)
            return super().select_poi_time(plan, root, day, clock, name, kind, recommended_visit_time)

        def constraints_validation(self, query, plan, root):
            if not self._set_done(plan):
                return False, plan
            for appointment in self._appointments():
                acts = plan[appointment["day"] - 1]["activities"]
                if not any(a.get("position") == appointment["name"]
                           and a["start_time"] == appointment["start_time"] for a in acts):
                    return False, plan
            count = len(self.pool.items)
            result = super().constraints_validation(query, plan, root)
            if len(self.pool.items) > count:
                self._set_candidates += 1
                if self._set_candidates >= self.day_schedule_limit:
                    self._set_status = "schedule_limit"
                    raise DaySearchComplete()
            return result

    return DaySetAgent

