"""Bounded, model-independent construction over the current WorldEnv data."""

from copy import deepcopy
from collections import Counter
import math
from time import perf_counter

from .preferences import CULTURE_TYPES, evaluate_preferences, normalize_profile
from .snapshot_constraints import required_features_present, ticket_matches


class SearchBudgetExpired(Exception):
    pass


def minutes(value):
    hour, minute = map(int, str(value).split(':'))
    if not 0 <= hour <= 24 or not 0 <= minute < 60 or hour == 24 and minute:
        raise ValueError('Invalid time')
    return hour * 60 + minute


def clock(value):
    return f'{int(value) // 60:02d}:{int(value) % 60:02d}'


def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except (ValueError, TypeError):
        return None


BUDGET_EXCLUSIONS = {
    'without_intercity': frozenset(('train', 'airplane')),
    'without_lodging': frozenset(('accommodation',)),
    'without_intercity_and_lodging': frozenset(('train', 'airplane', 'accommodation')),
}


def total_cost(activities, *, exclude=()):
    return sum((0 if a.get('type') in exclude else a['cost'])
               + sum(t['cost'] for t in a.get('transports', [])) for a in activities)


class Constructor:
    def __init__(self, request, env, max_seconds, max_candidates, on_candidate=None):
        self.request = request
        self.on_candidate = on_candidate
        self.env = env
        self.city = request['target_city']
        self.people = request['people_number']
        self.days = request['days']
        self.started = perf_counter()
        self.deadline = self.started + max_seconds
        self.max_candidates = max_candidates
        self.profile = request.get('profile') or normalize_profile()
        self.routes = {}
        self.route_calls = 0
        self.attempts = []
        self.candidates = []
        self.appointment_places = {a["place"] for a in request.get("appointments", ()) if a.get("confirmed")}
        self.points = env.poi.data.get(self.city, {})
        self.food = self.catalog(env.restaurants.data.get(self.city), 'restaurant')
        self.spots = self.catalog(env.attractions.data.get(self.city), 'attraction')
        self.hotels = self.catalog(env.accommodations.data.get(self.city), 'hotel')

    def check(self):
        if perf_counter() >= self.deadline:
            raise SearchBudgetExpired()

    def catalog(self, frame, kind):
        rows = []
        if frame is None:
            return rows
        excluded = set(self.request.get('excluded_attractions' if kind == 'attraction' else 'excluded_restaurants', ()))
        excluded_types = set(self.request.get('excluded_attraction_types' if kind == 'attraction' else 'excluded_restaurant_types', ()))
        cap = self.request.get('food_price_limit' if kind == 'restaurant' else 'hotel_price_limit')
        required_hotels = set(self.request.get('required_hotel_names', ()))
        party_hotel_cap = self.request.get('hotel_party_price_limit')
        for row in frame.to_dict('records'):
            price = number(row.get('price'))
            if price is None or row.get('name') in excluded or row.get('name') not in self.points:
                continue
            if kind != 'hotel' and str(row.get('type' if kind == 'attraction' else 'cuisine')) in excluded_types:
                continue
            if kind != 'attraction' and cap is not None and price > cap:
                continue
            row = dict(row, price=price)
            if kind == 'hotel':
                if required_hotels and required_hotels != {row['name']}:
                    continue
                beds = number(row.get('numbed'))
                features = self.request.get('required_hotel_features', ())
                options = self.request.get('hotel_feature_options', {})
                if features and not required_features_present(row.get('featurehoteltype'), features, options):
                    continue
                explicit_rooms = self.request.get('rooms')
                if self.request.get('reference_lodging'):
                    rooms = explicit_rooms or math.ceil(self.people / 2)
                else:
                    wanted = self.request.get('room_type')
                    if not beds or wanted is not None and beds != wanted:
                        continue
                    per_room = self.request.get('snapshot_room_occupancy', beds)
                    rooms = explicit_rooms or math.ceil(self.people / per_room)
                    if rooms * per_room < self.people:
                        continue
                if party_hotel_cap is not None and price * rooms > party_hotel_cap + 1e-6:
                    continue
                row['_rooms'] = rooms
                row['_bed_count'] = int(beds) if beds else None
            rows.append(row)
        return rows

    def distance(self, first, second):
        a, b = self.points.get(first), self.points.get(second)
        if a is None or b is None:
            return math.inf
        lat = math.radians((a[0] + b[0]) / 2)
        return 111 * math.hypot(a[0] - b[0], (a[1] - b[1]) * math.cos(lat))

    def route(self, start, end, depart, mode):
        self.check()
        if start == end:
            return []
        key = start, end, depart, mode
        if key not in self.routes:
            self.route_calls += 1
            try:
                raw = self.env.transportation.goto(self.city, start, end, clock(depart), mode)
                if not isinstance(raw, list) or not raw:
                    self.routes[key] = None
                else:
                    result = []
                    cursor = depart
                    for leg in raw:
                        begin, finish = minutes(leg['start_time']), minutes(leg['end_time'])
                        price = number(leg['cost'])
                        if begin < cursor or finish < begin or finish >= 1440 or price is None:
                            raise ValueError('Invalid route timeline or price')
                        leg = dict(leg, price=price)
                        factor = 1
                        if leg['mode'] == 'taxi':
                            factor = math.ceil(self.people / 4)
                            leg['cars'] = factor
                        elif leg['mode'] == 'metro':
                            factor = self.people
                            leg['tickets'] = factor
                        leg['cost'] = round(price * factor, 2)
                        result.append(leg)
                        cursor = finish
                    self.routes[key] = result
            except (ValueError, KeyError, TypeError, IndexError):
                self.routes[key] = None
        return deepcopy(self.routes[key])

    def routes_to(self, start, end, depart, deadline, style):
        found = []
        modes = self.request.get('local_modes', ('metro', 'taxi', 'walk'))
        for mode in modes:
            if mode == 'walk' and len(modes) > 1 and self.distance(start, end) > 2:
                continue
            route = self.route(start, end, depart, mode)
            if route is None or any(leg['mode'] not in modes for leg in route):
                continue
            finish = minutes(route[-1]['end_time']) if route else depart
            if finish <= deadline:
                cost = sum(t['cost'] for t in route)
                walk = sum(minutes(t['end_time']) - minutes(t['start_time']) for t in route if t['mode'] == 'walk')
                weight = self.profile['weights']
                score = (finish - depart) * weight['transport'] + walk * weight['walking'] + cost * weight['cost']
                if style in ('cheap', 'budget', 'focus_budget'):
                    score += cost * 3
                found.append((score, finish, route))
        return sorted(found, key=lambda x: x[0])

    def routes_to_appointment(self, start, end, depart, deadline, style):
        routes = self.routes_to(start, end, depart, deadline, style)
        if not routes or not routes[0][2]:
            return routes
        preferred_departure = depart + deadline - 15 - routes[0][1]
        if preferred_departure <= depart:
            return routes
        delayed = self.routes_to(start, end, preferred_departure, deadline, style)
        return delayed + routes

    def tickets(self, reverse=False):
        start, target = self.request['start_city'], self.city
        if reverse:
            start, target = target, start
        rows = []
        reasons = Counter()
        direction = 'return' if reverse else 'outbound'
        restrictions = self.request.get('ticket_restrictions', {}).get(direction, ())
        for kind in self.request.get('intercity_modes', ('train', 'airplane')):
            frame = self.env.intercitytransport.select(start, target, kind)
            if frame is None:
                continue
            for row in frame.to_dict('records'):
                self.check()
                if not all(ticket_matches(kind, row, options) for options in restrictions):
                    reasons['ticket_service_restriction'] += 1
                    continue
                try:
                    begin, end = minutes(row['BeginTime']), minutes(row['EndTime'])
                    price = number(row['Cost'])
                    duration = number(row.get('Duration'))
                    # Some trains arrive on a later day despite a later clock time.
                    if end <= begin or (duration and begin + duration * 60 > 1441):
                        reasons['cross_midnight'] += 1
                        continue
                    if price is None:
                        reasons['missing_price'] += 1
                        continue
                    if reverse:
                        departure_cap = self.request.get('return_departure_latest')
                        arrival_cap = self.request.get('return_arrival_latest')
                        if departure_cap and begin > minutes(departure_cap):
                            reasons['departure_deadline'] += 1
                            continue
                        if arrival_cap and end > minutes(arrival_cap):
                            reasons['arrival_deadline'] += 1
                            continue
                    else:
                        earliest, latest = self.request.get('outbound_earliest'), self.request.get('outbound_latest')
                        if earliest and begin < minutes(earliest) or latest and begin > minutes(latest):
                            reasons['outbound_window'] += 1
                            continue
                        stations = self.request.get('outbound_arrival_stations', ())
                        if stations and any(row['To'] != name for name in stations):
                            reasons['outbound_arrival_station'] += 1
                            continue
                        period = self.request.get('outbound_arrival_period')
                        if period and not minutes(period[0]) <= end < minutes(period[1]):
                            reasons['outbound_arrival_period'] += 1
                            continue
                    rows.append(dict(row, _kind=kind, _begin=begin, _end=end, Cost=price))
                except (TypeError, KeyError, ValueError):
                    reasons['invalid_record'] += 1
                    continue
        if not rows:
            self.attempts.append(dict(reason='no_return_ticket' if reverse else 'no_outbound_ticket',
                                      rejected=dict(reasons), same_day_tickets_only=True))
        def score(row):
            if reverse:
                inconvenient = max(0, 1080 - row['_begin']) + max(0, row['_end'] - 1320) * 2
                deviation = abs(row['_begin'] - 1140) * .2
            else:
                inconvenient = max(0, 420 - row['_begin']) * 2
                deviation = max(0, row['_end'] - 600) * .65
            return row['Cost'] * self.people * .6 + inconvenient + deviation
        rows.sort(key=score)
        representatives, seen_windows = [], set()
        for row in rows:
            window = row['_begin' if reverse else '_end'] // 180
            if window not in seen_windows:
                representatives.append(row)
                seen_windows.add(window)
        return (representatives + [row for row in rows if row not in representatives])[:10]

    def ticket_activity(self, row, route=None):
        kind = row['_kind']
        key = 'TrainID' if kind == 'train' else 'FlightID'
        return dict(type=kind, start_time=row['BeginTime'], end_time=row['EndTime'],
                    start=row['From'], end=row['To'], price=row['Cost'],
                    cost=round(row['Cost'] * self.people, 2), tickets=self.people,
                    transports=route or [], **{key: str(row[key])})

    def poi_activity(self, row, kind, start, end, route):
        price = row['price']
        item = dict(position=row['name'], type=kind, price=price, cost=price * self.people,
                    start_time=clock(start), end_time=clock(end), transports=route)
        if kind == 'attraction':
            item['tickets'] = self.people
        return item

    def select(self, kind, position, now, deadline, earliest, used, style, terminal, terminal_deadline):
        attraction = kind == 'attraction'
        meal_rejections = Counter() if not attraction and self.request.get('explain_meal_failure') else None
        if meal_rejections is not None:
            self.meal_rejections = meal_rejections
        rows = self.spots if attraction else self.food
        must = set(self.request.get('must_attractions' if attraction else 'must_restaurants', ())) - used
        preferred = set(self.request.get('preferred_attractions' if attraction else 'preferred_restaurants', ()))
        types = self.profile['interest_types' if attraction else 'restaurant_types']
        field = 'type' if attraction else 'cuisine'
        requested_types = set(self.request.get('must_attraction_types' if attraction else 'must_restaurant_types', ()))
        visited_types = {str(r.get(field)) for r in rows if r['name'] in used}
        missing_types = requested_types - visited_types
        preferred_types = self.request.get('preferred_attraction_types' if attraction else 'preferred_restaurant_types', ())
        culture = attraction and "culture" in self.request.get("preference_dimensions", ())
        day_hints = self.request.get('day_preferences', {}).get(getattr(self, 'current_day', None), {})
        primary = set(day_hints.get('attractions' if attraction else 'restaurants', ()))
        if not attraction and self.request.get('try_preferred_meal_once') and primary & used:
            primary.clear()
        alternatives = set(day_hints.get('alternatives', ())) if attraction else set()
        other_cores = ({name for day, hints in self.request['day_preferences'].items()
                        if day != self.current_day for name in hints.get('attractions', ())} - primary
                       if attraction and day_hints else set())

        def rank(row):
            name = row['name']
            category = str(row.get(field, ''))
            type_match = (culture and category in CULTURE_TYPES) or any(t in category for t in (*types, *preferred_types))
            geography = self.distance(position, name)
            price_weight = .06 if style == 'cheap' else .015
            low_cost = row['price'] if style in ('budget', 'focus_budget') else 0
            day_rank = 3 if name in other_cores else 0 if name in primary else 1 if name in alternatives else 2
            protect_core = 0 if style == 'focus_budget' and attraction and name in primary else 1
            return (name not in must, str(row.get(field)) not in missing_types, protect_core, low_cost, day_rank,
                    name not in preferred, not type_match,
                    geography + row['price'] * price_weight)
        visit_days = self.request.get('earliest_required_visit_days', {}) if attraction else {}
        day = getattr(self, 'current_day', 0)
        available = [r for r in rows if r['name'] not in used and
                     (not attraction or r['name'] not in self.appointment_places)
                     and visit_days.get(r['name'], 0) <= day]
        if attraction and self.request.get('prefer_regional_activities'):
            nearby = primary | alternatives | must
            available = [r for r in available if r['name'] in nearby]
        options = sorted(available, key=rank)[:14]
        if not attraction and terminal and self.request.get('terminal_area_meals'):
            tried = {r['name'] for r in options}
            options.extend(sorted((r for r in available if r['name'] not in tried),
                                  key=lambda r: self.distance(r['name'], terminal))[:10])
        valid = []
        for row in options:
            self.check()
            try:
                opens, closes = minutes(row['opentime']), minutes(row['endtime'])
                if closes <= opens:
                    closes = 1440
                length = 90 if attraction else 60
                if attraction:
                    minimum = number(row.get('recommendmintime'))
                    if minimum is not None and minimum > 0:
                        length = max(60, min(180, int(minimum * 60)))
                suggested = self.request.get('experience_earliest', {}).get(row['name']) if attraction else None
                available_from = max(earliest, minutes(suggested)) if suggested else earliest
                if suggested and available_from + length > min(deadline, closes):
                    continue
                routes = self.routes_to(position, row['name'], now, min(deadline, closes) - length, style)
                if not routes and meal_rejections is not None:
                    earliest_start = max(now, earliest, opens)
                    reason = ('opening_or_meal_window' if earliest_start + length > min(deadline, closes)
                              or earliest_start >= (840 if kind == 'lunch' else 1200)
                              else 'route_to_meal_within_window')
                    meal_rejections[reason] += 1
                for _, arrival, route in routes[:1]:
                    begin = max(arrival, available_from, opens, 480 if attraction else 0)
                    end = begin + length
                    if end > min(deadline, closes):
                        if meal_rejections is not None:
                            meal_rejections['arrival_misses_meal_window'] += 1
                        continue
                    meal_latest = 840 if kind == 'lunch' else 1200
                    if not attraction and begin >= meal_latest:
                        if meal_rejections is not None:
                            meal_rejections['arrival_misses_meal_window'] += 1
                        continue
                    if route and begin - arrival > 60:
                        delayed = self.routes_to(position, row['name'], now + begin - arrival,
                                                 min(deadline, closes) - length, style)
                        if delayed:
                            _, arrival, route = delayed[0]
                            begin = max(arrival, available_from, opens, 480 if attraction else 0)
                            end = begin + length
                            if not attraction and begin >= meal_latest:
                                if meal_rejections is not None:
                                    meal_rejections['arrival_misses_meal_window'] += 1
                                continue
                    if terminal and not self.routes_to(row['name'], terminal, end, terminal_deadline, style):
                        if meal_rejections is not None:
                            meal_rejections['meal_to_next_required_stop'] += 1
                        continue
                    item = self.poi_activity(row, kind, begin, end, route)
                    valid.append((rank(row), total_cost([item]), item))
            except (TypeError, KeyError, ValueError):
                if meal_rejections is not None:
                    meal_rejections['invalid_restaurant_record'] += 1
                continue
            if len(valid) >= 3:
                break
        if not valid:
            return None
        return min(valid, key=lambda x: (x[0], x[1]))[2]

    def appointment_tasks(self, now, day_end, appointments):
        """Allocate flexible activities in the gaps around fixed visits."""
        tasks, meals = [], set()
        for anchor in [*appointments, None]:
            limit = minutes(anchor["start_time"]) if anchor else day_end
            terminal = (anchor["place"], limit) if anchor else None
            for kind, opens, last_start, closes in (
                    ("lunch", 660, 840, 900), ("dinner", 1020, 1200, 1260)):
                meal_earliest, meal_end = max(now, opens), min(limit, closes)
                if kind in meals or meal_earliest >= last_start or meal_earliest + 60 > meal_end:
                    continue
                tasks.append(("attraction", now, min(meal_end - 60, last_start - 1), terminal))
                tasks.append((kind, meal_earliest, meal_end, terminal))
                meals.add(kind)
            tasks.append(("attraction", now, limit, terminal))
            if anchor:
                now = minutes(anchor["end_time"])
                tasks.append(("appointment", limit, now, anchor))
        return tasks

    def fixed_visit(self, anchor, position, now, style):
        start, end = minutes(anchor["start_time"]), minutes(anchor["end_time"])
        for row in self.spots:
            if row["name"] != anchor["place"]:
                continue
            try:
                opens, closes = minutes(row["opentime"]), minutes(row["endtime"])
            except (KeyError, ValueError):
                continue
            if closes <= opens:
                closes += 1440
            if opens <= start < end <= closes:
                routes = self.routes_to_appointment(position, anchor["place"], now, start, style)
                if routes:
                    return self.poi_activity(row, "attraction", start, end, routes[0][2])
        return None

    def construct(self, go, back, hotel, style):
        self.meal_failure = None
        itinerary, used_spots, used_food = [], set(), set()
        fixed = [(go['_kind'], go['Cost'] * self.people), (back['_kind'], back['Cost'] * self.people)]
        if hotel:
            fixed.append(('accommodation', hotel['price'] * hotel['_rooms'] * (self.days - 1)))
        cap = self.request.get('budget')
        scoped_budgets = self.request.get('scoped_budgets', {})
        if cap is not None and sum(cost for _, cost in fixed) > cap:
            return None, 'fixed_budget'
        if any(sum(cost for kind, cost in fixed if kind not in BUDGET_EXCLUSIONS[scope]) > limit
               for scope, limit in scoped_budgets.items()):
            return None, 'scoped_budget'
        stops = {'relaxed': 2, 'balanced': 3, 'full': 4}[self.profile['pace']]
        if style in ('short', 'budget', 'focus_budget'):
            stops = min(stops, 2)
        for day in range(1, self.days + 1):
            self.check()
            self.current_day = day
            activities = [self.ticket_activity(go)] if day == 1 else []
            now = go['_end'] if day == 1 else 510
            position = go['To'] if day == 1 else hotel['name']
            last = day == self.days
            terminal = back['From'] if last else hotel['name']
            terminal_deadline = back['_begin'] - (60 if back['_kind'] == 'train' else 120) if last else 1320
            if last and day > 1:
                now = min(now, max(0, terminal_deadline - 180))
            day_end = min(1260, terminal_deadline)
            available_start = now
            if now >= terminal_deadline:
                return None, 'arrival_return_window'
            anchors = sorted((a for a in self.request.get("appointments", ())
                              if a.get("confirmed") and a["day"] == day), key=lambda a: a["start_time"])
            if any(minutes(a["start_time"]) < now or minutes(a["end_time"]) > terminal_deadline for a in anchors):
                return None, "appointment_window"
            lunch = max(now, 660) + 60 <= min(day_end, 900)
            dinner = max(now, 1020) + 60 <= min(day_end, 1260)
            count = 0
            tasks = [('attraction', 480, min(day_end, 780 if lunch else 990))]
            if lunch:
                tasks.append(('lunch', 660, min(day_end, 900)))
            tasks.append(('attraction', 480, min(day_end, 990 if dinner else day_end)))
            if dinner:
                tasks.append(('dinner', 1020, min(day_end, 1260)))
            tasks.extend([('attraction', 480, day_end)] * max(0, stops - 2))
            tasks = (self.appointment_tasks(now, day_end, anchors) if anchors else
                     [(kind, begin, end, None) for kind, begin, end in tasks])
            optional_count = 0
            max_optional = max(0, min(stops, self.profile["hard_limits"].get("max_daily_attractions", stops)) - len(anchors))
            day_hints = self.request.get('day_preferences', {}).get(day)
            if day_hints and style != 'budget':
                max_optional = min(max_optional, max(0, len(day_hints['attractions']) - len(anchors)))
            for kind, earliest, end, target in tasks:
                if kind == "appointment":
                    activity = self.fixed_visit(target, position, now, style)
                    if activity is None:
                        return None, "appointment_access"
                    activities.append(activity)
                    used_spots.add(activity["position"])
                    now, position = end, activity["position"]
                    count += 1
                    continue
                if kind == 'attraction' and optional_count >= max_optional:
                    continue
                if now + (60 if kind != 'attraction' else 90) > end:
                    if kind != 'attraction':
                        return None, 'meal_window'
                    continue
                used = used_spots if kind == 'attraction' else used_food
                stop, cutoff = target or (terminal, terminal_deadline)
                activity = self.select(kind, position, now, end, earliest, used, style, stop, cutoff)
                if activity is None:
                    if kind != 'attraction':
                        if self.request.get('explain_meal_failure'):
                            self.meal_failure = {'day': day, 'meal': kind, 'from': position,
                                                 'previous_end': clock(now), 'window_end': clock(end),
                                                 'next_required_stop': stop, 'next_deadline': clock(cutoff),
                                                 'restaurant_rejections': dict(self.meal_rejections)}
                        return None, 'meal_access'
                    continue
                activities.append(activity)
                used.add(activity['position'])
                now, position = minutes(activity['end_time']), activity['position']
                count += kind == 'attraction'
                optional_count += kind == 'attraction'
            if count == 0 and day_end - available_start >= 360 and (not day_hints or day_hints['attractions']):
                return None, 'no_daily_activity'
            access = self.routes_to(position, terminal, now, terminal_deadline, style)
            if not access:
                return None, 'terminal_access'
            _, arrival, route = access[0]
            if last:
                # Retain free time in the city instead of waiting hours inside a terminal.
                depart = now + terminal_deadline - arrival
                if route and depart > now:
                    delayed = self.routes_to(position, terminal, depart, terminal_deadline, style)
                    if delayed:
                        _, arrival, route = delayed[0]
                activities.append(self.ticket_activity(back, route))
            else:
                price = hotel['price']
                item = dict(position=hotel['name'], type='accommodation', price=price,
                            cost=price * hotel['_rooms'], rooms=hotel['_rooms'],
                            start_time=clock(arrival), end_time='24:00', transports=route)
                if hotel['_bed_count'] is not None:
                    item['room_type'] = hotel['_bed_count']
                activities.append(item)
            itinerary.append(dict(day=day, activities=activities))
            if cap is not None and sum(total_cost(d['activities']) for d in itinerary) > cap:
                return None, 'actual_budget'
            if any(sum(total_cost(d['activities'], exclude=BUDGET_EXCLUSIONS[scope]) for d in itinerary)
                   > limit + 1e-7 for scope, limit in scoped_budgets.items()):
                return None, 'scoped_budget'
        if not used_spots:
            return None, 'no_attractions'
        if set(self.request.get('must_attractions', ())) - used_spots:
            return None, 'missing_required_attraction'
        if set(self.request.get('must_restaurants', ())) - used_food:
            return None, 'missing_required_restaurant'
        for key, rows, used, field in (('must_attraction_types', self.spots, used_spots, 'type'),
                                      ('must_restaurant_types', self.food, used_food, 'cuisine')):
            observed = {str(r.get(field)) for r in rows if r['name'] in used}
            if set(self.request.get(key, ())) - observed:
                return None, 'missing_' + key
        return dict(people_number=self.people, start_city=self.request['start_city'],
                    target_city=self.city, itinerary=itinerary), None

    def run(self):
        stopped = False
        try:
            gos, backs = self.tickets(), self.tickets(True)
            missing = []
            if not self.spots:
                missing.append('no_compatible_attraction')
            if not self.food:
                missing.append('no_compatible_restaurant')
            if self.days > 1 and not self.hotels:
                missing.append('no_compatible_hotel')
            self.attempts.extend(dict(reason=reason) for reason in missing)
            if missing:
                gos = []
            successful_windows = set()
            search_hotels = set(self.request.get('search_hotel_names', ()))
            hotel_pool = [h for h in self.hotels if h['name'] in search_hotels] or self.hotels
            regional_anchors = self.request.get('preferred_attractions', ())
            if self.request.get('prefer_regional_activities') and regional_anchors:
                hotel_pool = sorted(hotel_pool, key=lambda h: sum(
                    self.distance(h['name'], anchor) for anchor in regional_anchors))[:24]
            prefer_windows = self.request.get('prefer_activity_windows')
            hints = self.request.get('day_preferences', {})
            first_has_visit = bool(hints.get(1, {}).get('attractions'))
            last_has_visit = bool(hints.get(self.days, {}).get('attractions'))
            arrival_access = {}
            if arrival_anchor := self.request.get('arrival_access_anchor'):
                for go in gos:
                    key = go['To'], go['_end']
                    if key not in arrival_access:
                        routes = self.routes_to(go['To'], arrival_anchor, go['_end'], 1260, 'local')
                        arrival_access[key] = routes[0][1] - go['_end'] if routes else 720
            for style in self.request.get('search_styles', ('local', 'cheap', 'short')):
                before_style = len(self.candidates)
                # Interleave locations and ticket windows rather than exhaust one root.
                combinations = []
                for go in gos:
                    transfer_penalty = 2 * arrival_access.get((go['To'], go['_end']), 0)
                    anchors = self.request.get('must_attractions') or self.request.get('preferred_attractions') or (go['To'],)
                    suggested_hotels = set(self.request.get('preferred_hotels', ()))
                    hotels = sorted(hotel_pool, key=lambda h: h['price'] * h['_rooms'] *
                                    (.15 if style in ('budget', 'focus_budget') else .03) +
                                    sum(self.distance(h['name'], a) for a in anchors) / len(anchors)
                                    - (2 if h['name'] in suggested_hotels else 0))[:8] if self.days > 1 else [None]
                    for back in backs:
                        for hotel in hotels:
                            fixed = (go['Cost'] + back['Cost']) * self.people + (hotel['price'] * hotel['_rooms'] * (self.days - 1) if hotel else 0)
                            cutoff = back['_begin'] - (60 if back['_kind'] == 'train' else 120)
                            if self.days == 1:
                                useful = max(0, min(1200, cutoff) - max(480, go['_end']))
                            else:
                                useful = max(0, 1200 - max(480, go['_end']))
                                useful += max(0, min(1200, cutoff) - 510) + max(0, self.days - 2) * 690
                            target = self.days * (360 if self.profile['pace'] == 'relaxed' else 480)
                            inconvenience = max(0, 420 - go['_begin']) * 2 + max(0, back['_end'] - 1320) * 2
                            cost_weight = 1.2 if style in ('budget', 'focus_budget') else .8 if style == 'cheap' else .25
                            score = fixed * cost_weight - min(useful, target) * .65 + inconvenience + transfer_penalty
                            window = go['_end'] // 180, back['_begin'] // 180
                            short_days = 0
                            if prefer_windows:
                                if self.days == 1:
                                    short_days = int(first_has_visit and useful < 180)
                                else:
                                    short_days = (int(first_has_visit and go['_end'] > 1020)
                                                  + int(last_has_visit and cutoff < 690))
                            combinations.append(((short_days, score), window, go, back, hotel))
                attempted_windows = Counter()
                cover_more_windows = self.request.get('terminal_area_meals')
                for _ in range(min(18, len(combinations))):
                    self.check()
                    selected = min(range(len(combinations)),
                                   key=lambda idx: (combinations[idx][1] in successful_windows,
                                                    attempted_windows[combinations[idx][1]] if cover_more_windows else 0,
                                                    combinations[idx][0]))
                    _, window, go, back, hotel = combinations.pop(selected)
                    if cover_more_windows:
                        attempted_windows[window] += 1
                    result, reason = self.construct(go, back, hotel, style)
                    self.attempts.append(dict(style=style, outbound=go.get('TrainID', go.get('FlightID')),
                                              return_id=back.get('TrainID', back.get('FlightID')),
                                              hotel=hotel['name'] if hotel else None, reason=reason))
                    if self.meal_failure:
                        self.attempts[-1]['meal_failure'] = self.meal_failure
                    if result is not None and result not in self.candidates:
                        self.candidates.append(result)
                        successful_windows.add(window)
                        if self.on_candidate is not None:
                            self.on_candidate(result)
                    if len(self.candidates) >= self.max_candidates:
                        break
                    if len(self.candidates) - before_style >= math.ceil(self.max_candidates / 3):
                        break
                if len(self.candidates) >= self.max_candidates:
                    break
        except SearchBudgetExpired:
            stopped = True
        ranked = [(evaluate_preferences(p, self.profile, self.env, self.request), p) for p in self.candidates]
        eligible = [(e, p) for e, p in ranked if e.get('eligible')]
        best = max(eligible, key=lambda pair: pair[0].get('score') or 0)[1] if eligible else None
        return dict(status='success' if best else 'budget_exhausted' if stopped else 'no_feasible_plan',
                    plan=best, candidates=self.candidates, diagnostics=self.attempts,
                    metrics=dict(seconds=perf_counter() - self.started, route_calls=self.route_calls,
                                 attempts=len(self.attempts), candidates=len(self.candidates),
                                 budget_exhausted=stopped),
                    protocol='bounded_same_day_tickets_v1')


def plan(request, env, max_seconds=15, max_candidates=5, on_candidate=None):
    """Share baseline candidates before extending the pool for a personal profile.

    Default construction returns at most ``max_candidates``. Personalization can
    add the same number, for at most ``2 * max_candidates`` distinct candidates.
    Both phases consume a single wall-clock allowance. Keeping every candidate
    lets the service independently validate before making its final selection.
    """
    if hasattr(request, 'to_dict'):
        request = request.to_dict()
    balanced = normalize_profile()
    actual = request.get('profile') or balanced
    if actual == balanced:
        return Constructor(request, env, max_seconds, max_candidates, on_candidate).run()

    started = perf_counter()
    deadline = started + max_seconds
    # Personal hard limits belong to the request even during neutral generation.
    balanced['hard_limits'] = deepcopy(actual.get('hard_limits', {}))
    neutral_request = {**request, 'profile': balanced}
    neutral = Constructor(neutral_request, env, max(0, deadline - perf_counter()), max_candidates, on_candidate)
    neutral_result = neutral.run()
    phases = [('balanced', neutral_result)]
    remaining = deadline - perf_counter()
    if remaining > 0:
        personalized = Constructor(request, env, remaining, max_candidates, on_candidate)
        personalized.routes = neutral.routes
        phases.append(('personalized', personalized.run()))

    candidates, diagnostics = [], []
    for name, result in phases:
        diagnostics.extend({**item, 'generation_phase': name} for item in result['diagnostics'])
        for candidate in result['candidates']:
            if candidate not in candidates:
                candidates.append(candidate)
    assessed = [(evaluate_preferences(candidate, actual, env, request), candidate) for candidate in candidates]
    eligible = [(review, candidate) for review, candidate in assessed if review.get('eligible')]
    best = max(eligible, key=lambda pair: pair[0].get('score') or 0)[1] if eligible else None
    stopped = any(result['metrics']['budget_exhausted'] for _, result in phases)
    return dict(status='success' if best else 'budget_exhausted' if stopped else 'no_feasible_plan',
                plan=best, candidates=candidates, diagnostics=diagnostics,
                metrics=dict(seconds=perf_counter() - started,
                             route_calls=sum(result['metrics']['route_calls'] for _, result in phases),
                             attempts=sum(result['metrics']['attempts'] for _, result in phases),
                             candidates=len(candidates), budget_exhausted=stopped,
                             candidate_limit=max_candidates * 2,
                             generation_phases={name: result['metrics'] for name, result in phases}),
                protocol='bounded_same_day_tickets_shared_candidates_v3')
