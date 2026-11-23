"""Pure, offline feature scoring. No planner, environment, network or model imports.

``score_plan`` accepts the ordinary ShortTrip plan/query dictionaries. ``config``
is the quality section, with optional keys from DEFAULT_CONFIG. Times are HH:MM
(24:00 is permitted as an endpoint); days are one-based. ``day_windows`` maps day
strings to lists of [start, end]; ``locked_intervals`` contains dictionaries with
day/start_time/end_time and optionally end_day_offset. A supplied ``trip_bounds``
has start_day/start_time/end_day/end_time and overrides inferred arrival/departure.
An explicit ``available_intervals`` mapping replaces inferred availability but
still subtracts locked intervals. Never derive availability from sightseeing count.

Resources are optional dictionaries: attractions/hotels keyed by position or id.
Attraction fields: tags (list), intensity (nonnegative number); hotel fields:
city, stars, facilities (list), price. Explicit plan fields may also supply these.
resources['intercity'] may be keyed by TrainID/FlightID with duration_minutes.
Unknown resource fields are never classified using an LLM or a name heuristic.

Reviewed requirements: {'reviewed': True, 'items': [...]} (empty means no soft
preferences). Each rule soft item needs id, kind='soft', method='rule', feature,
mapping, thresholds (dict), aggregation, missing_policy='unknown', evidence
(nonempty list); weight defaults to 1. Optional days is 'all' or a list of days.
Supported feature / aggregation combinations:
  attraction_tags / unique_attractions, mapping='tag_fraction', any_of=[tags];
  pace / available_minutes, mapping='pace', lower/upper in [0,1];
  walking_minutes, walking_distance_km, activity_intensity / day_equal;
  hotel_stars, hotel_price, hotel_facilities, hotel_city / night_weighted;
  scalar metrics such as transport_minutes, total_cost / total.
Numerical mappings: upper_bound/lower_bound (target, positive scale), interval
(lower, upper, positive lower_scale/upper_scale). binary uses equals, or all_of
for hotel_facilities (each requested facility is equally weighted).
Reviewed pace requirements also replace the default R target on their scope.
Semantic soft items need id, method='semantic', kind='soft', evidence and weight.
Their ratings (and experience E) must have known status, integer 0..4 and evidence.
Only full_v0 uses semantic input. N/A differs from unknown/unconfigured throughout.

The caller gates these scores on independent feasibility. These functions do not
certify legality, and malformed/missing timing returns unknown, not a valid plan.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from math import isfinite


DEFAULT_CONFIG = {
    'quality_weights': {'T': .30, 'R': .25, 'H': .10, 'U': .10, 'E': .25},
    'activity_window': ['08:00', '21:00'],
    'day_windows': {},
    'locked_intervals': [],
    'available_intervals': None,
    'trip_bounds': None,
    'pace_target': [.4, .8],
    'applicability': {'T': True, 'R': True, 'H': None, 'U': True, 'E': True},
    'required_nights': None,
    'necessary_revisits': [],
    'necessary_hotel_switches': [],
}
INTERCITY = {'train', 'airplane', 'flight'}
HOTELS = {'accommodation', 'hotel'}
MEALS = {'breakfast', 'lunch', 'dinner', 'restaurant'}
LOCAL = {'transport', 'innercity_transport', 'walk', 'metro', 'taxi', 'bus', 'drive', 'cycling'}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _clock(value):
    if not isinstance(value, str) or value.count(':') != 1:
        raise ValueError('Expected HH:MM')
    h, m = value.split(':')
    if not (h.isdigit() and m.isdigit()):
        raise ValueError('Expected HH:MM')
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m < 60 and (h < 24 or m == 0)):
        raise ValueError('Clock outside 00:00..24:00')
    return h * 60 + m


def _day(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError('Day must be a positive integer')
    return value


def _interval(item, day, *, intercity=False, arrival=None, resource=None):
    """Use explicit dates/durations; only local clock rollover is unambiguous."""
    day = _day(item.get('day', day))
    start_clock, end_clock = _clock(item['start_time']), _clock(item['end_time'])
    start = (day - 1) * 1440 + start_clock
    duration = item.get('duration_minutes', (resource or {}).get('duration_minutes'))
    if duration is not None:
        if not _number(duration) or duration < 0:
            raise ValueError('Invalid duration_minutes')
        end = start + duration
        if abs((end % 1440) - (end_clock % 1440)) > 1e-6:
            raise ValueError('Duration conflicts with end clock')
    elif 'end_day' in item:
        end = (_day(item['end_day']) - 1) * 1440 + end_clock
    elif 'end_day_offset' in item:
        offset = item['end_day_offset']
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError('Invalid end_day_offset')
        end = start - start_clock + offset * 1440 + end_clock
    else:
        end = (day - 1) * 1440 + end_clock
        if end <= start and intercity:
            raise ValueError('Intercity rollover/equal clocks need explicit date or duration')
        if end < start:
            end += 1440
    if arrival is not None and not any(k in item for k in ('day', 'end_day', 'end_day_offset')):
        shift = ((arrival - end) // 1440) * 1440
        start, end = start + shift, end + shift
    if end < start or start < 0:
        raise ValueError('Invalid interval')
    return start, end


def _union(intervals):
    result = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _length(intervals):
    return sum(end - start for start, end in _union(intervals))


def _intersection(left, right):
    return _union([(max(a, c), min(b, d)) for a, b in left for c, d in right
                   if max(a, c) < min(b, d)])


def _subtract(intervals, blocked):
    result = _union(intervals)
    for lo, hi in _union(blocked):
        new = []
        for a, b in result:
            if b <= lo or a >= hi:
                new.append((a, b))
            else:
                if a < lo:
                    new.append((a, lo))
                if hi < b:
                    new.append((hi, b))
        result = new
    return result


def _clip(value):
    return min(1., max(0., value))


def _pace(value, lower, upper):
    if not all(_number(v) for v in (lower, upper)) or not 0 <= lower <= upper <= 1:
        raise ValueError('Pace requires 0 <= lower <= upper <= 1')
    value = _clip(value)
    if value < lower:
        return value / lower
    if value > upper:
        return (1 - value) / (1 - upper)
    return 1.


def _component(score=None, status='unknown', reason=None, **extra):
    return dict(score=score, status=status, reason=reason, **extra)


def _identity(activity):
    return activity.get('position') or activity.get('id') or activity.get('name')


def _resource(resources, category, item):
    collection = resources.get(category, {})
    if not isinstance(collection, dict):
        return {}
    keys = (_identity(item), item.get('TrainID'), item.get('FlightID'), item.get('id'))
    return next((collection[k] for k in keys if k is not None and isinstance(collection.get(k), dict)), {})


def _field(row, resources, category, name):
    value = _resource(resources, category, row['item']).get(name)
    return row['item'].get(name) if value is None else value


def _config(config):
    out = deepcopy(DEFAULT_CONFIG)
    for key, value in (config or {}).items():
        if key not in out:
            raise ValueError(f'Unknown quality config: {key}')
        if key in ('quality_weights', 'applicability'):
            if not isinstance(value, dict) or set(value) - set(out[key]):
                raise ValueError(f'Invalid {key}')
            out[key].update(value)
        else:
            out[key] = deepcopy(value)
    if any(not _number(v) or v < 0 for v in out['quality_weights'].values()):
        raise ValueError('Quality weights must be finite and nonnegative')
    if any(v is not None and not isinstance(v, bool) for v in out['applicability'].values()):
        raise ValueError('Applicability entries must be boolean or null')
    if any(out['applicability'][k] is None for k in ('T', 'R', 'U', 'E')):
        raise ValueError('Only H permits automatic applicability')
    if not isinstance(out['pace_target'], (list, tuple)) or len(out['pace_target']) != 2:
        raise ValueError('pace_target needs two endpoints')
    _pace(.5, *out['pace_target'])
    if out['required_nights'] is not None and (not isinstance(out['required_nights'], int)
            or isinstance(out['required_nights'], bool) or out['required_nights'] < 0):
        raise ValueError('required_nights must be nonnegative integer or null')
    return out


def _parse(plan, resources, issues):
    rows, legs = [], []
    flags = {'timeline': True, 'transport': True, 'distance': True, 'walking': True,
             'walking_distance': True, 'cost': True}
    days = plan.get('itinerary')
    if not isinstance(days, list):
        issues.append({'code': 'missing_itinerary'})
        return rows, legs, {k: False for k in flags}
    previous = None
    for di, day in enumerate(days):
        try:
            day_number = _day(day.get('day', di + 1))
        except (AttributeError, ValueError):
            flags['timeline'] = False
            issues.append({'code': 'invalid_day', 'index': di})
            continue
        activities = day.get('activities')
        if not isinstance(activities, list):
            flags['timeline'] = flags['transport'] = False
            issues.append({'code': 'missing_activities', 'day': day_number})
            continue
        for ai, item in enumerate(activities):
            if not isinstance(item, dict):
                flags['timeline'] = flags['transport'] = False
                issues.append({'code': 'invalid_activity', 'day': day_number, 'index': ai})
                continue
            row = {'id': item.get('id') or f'd{day_number}:a{ai}', 'day': day_number,
                   'item': item, 'kind': item.get('type'), 'interval': None, 'legs': []}
            try:
                row['interval'] = _interval(item, day_number, intercity=row['kind'] in INTERCITY,
                                             resource=_resource(resources, 'intercity', item))
            except (KeyError, TypeError, ValueError) as exc:
                flags['timeline'] = False
                issues.append({'code': 'unknown_activity_time', 'activity': row['id'], 'reason': str(exc)})
            rows.append(row)
            if row['kind'] in LOCAL:
                transport_items = [item]
            else:
                transport_items = item.get('transports')
            if not isinstance(transport_items, list):
                flags['transport'] = flags['distance'] = flags['walking'] = flags['walking_distance'] = False
                issues.append({'code': 'missing_transport_data', 'activity': row['id']})
                transport_items = []
            elif not transport_items and previous is not None:
                origin = previous['item'].get('end') if previous['kind'] in INTERCITY else _identity(previous['item'])
                destination = item.get('start') if row['kind'] in INTERCITY else _identity(item)
                if origin and destination and origin != destination:
                    flags['transport'] = flags['distance'] = flags['walking'] = flags['walking_distance'] = False
                    issues.append({'code': 'missing_movement', 'activity': row['id'], 'from': origin, 'to': destination})
            cursor = row['interval'][0] if row['interval'] else None
            parsed = []
            for li, leg in reversed(list(enumerate(transport_items))):
                segment = {'activity': row['id'], 'day': day_number, 'interval': None, 'item': leg}
                if not isinstance(leg, dict):
                    flags['transport'] = flags['distance'] = flags['walking'] = flags['walking_distance'] = False
                    issues.append({'code': 'invalid_transport', 'activity': row['id'], 'leg': li})
                    continue
                try:
                    segment['interval'] = (row['interval'] if row['kind'] in LOCAL else
                                            _interval(leg, day_number, arrival=cursor))
                    if segment['interval'] is None:
                        raise ValueError('Missing parent activity time')
                    cursor = segment['interval'][0]
                except (KeyError, TypeError, ValueError) as exc:
                    flags['transport'] = False
                    issues.append({'code': 'unknown_transport_time', 'activity': row['id'], 'leg': li, 'reason': str(exc)})
                mode = leg.get('mode', row['kind'] if row['kind'] in LOCAL else None)
                segment['mode'] = mode
                if not mode:
                    flags['walking'] = flags['walking_distance'] = False
                if mode == 'walk' and segment['interval'] is None:
                    flags['walking'] = False
                distance = leg.get('distance')
                if not _number(distance) or distance < 0:
                    flags['distance'] = False
                    if mode == 'walk':
                        flags['walking_distance'] = False
                parsed.append(segment)
            row['legs'] = list(reversed(parsed))
            legs.extend(row['legs'])
            previous = row
    return rows, legs, flags


def _windows(config, days):
    intervals = []
    explicit = config['available_intervals']
    for day in range(1, days + 1):
        mapping = explicit if explicit is not None else config['day_windows']
        windows = mapping.get(str(day), mapping.get(day, [] if explicit is not None else [config['activity_window']]))
        intervals.extend(_interval({'start_time': a, 'end_time': b}, day) for a, b in windows)
    # Overnight windows can cross a calendar boundary, but cannot duplicate minutes
    # in the next day's window or extend beyond the requested trip days.
    merged = _union(intervals)
    return {day: _intersection(merged, [((day - 1) * 1440, day * 1440)])
            for day in range(1, days + 1)}


def _availability(rows, query, config, days, issues):
    try:
        windows = _windows(config, days)
        blocked = [_interval(item, item['day']) for item in config['locked_intervals']]
        if config['available_intervals'] is None:
            bound = config['trip_bounds']
            if bound is not None:
                start = (_day(bound['start_day']) - 1) * 1440 + _clock(bound['start_time'])
                end = (_day(bound['end_day']) - 1) * 1440 + _clock(bound['end_time'])
                if end < start:
                    raise ValueError('Reversed trip_bounds')
            elif query.get('start_city') and query.get('start_city') == query.get('target_city'):
                start, end = 0, days * 1440
            else:
                transports = [r for r in rows if r['kind'] in INTERCITY]
                if len(transports) != 2 or any(r['interval'] is None for r in transports):
                    raise ValueError('Availability needs two dated intercity tickets or explicit trip_bounds')
                first, last = transports
                start, end = first['interval'][1], last['interval'][0]
                after = rows[rows.index(first) + 1:]
                if after and after[0]['legs']:
                    if any(l['interval'] is None for l in after[0]['legs']):
                        raise ValueError('Arrival access time unknown')
                    start = max(start, max(l['interval'][1] for l in after[0]['legs']))
                if last['legs']:
                    if any(l['interval'] is None for l in last['legs']):
                        raise ValueError('Departure access time unknown')
                    end = min(end, min(l['interval'][0] for l in last['legs']))
                if end < start:
                    raise ValueError('Arrival follows departure')
            windows = {d: _intersection(v, [(start, end)]) for d, v in windows.items()}
        return {d: _subtract(v, blocked) for d, v in windows.items()}
    except (KeyError, TypeError, ValueError) as exc:
        issues.append({'code': 'unknown_availability', 'reason': str(exc)})
        return None


def _scope(item, days):
    scope = item.get('days', 'all')
    if scope == 'all':
        return list(range(1, days + 1))
    if not isinstance(scope, list) or not scope or any(not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= days for d in scope):
        raise ValueError('Invalid preference day scope')
    return sorted(set(scope))


def _metrics(rows, legs, flags, available, days, resources, issues):
    traffic = [l['interval'] for l in legs if l['interval'] is not None]
    walk = [l['interval'] for l in legs if l['mode'] == 'walk' and l['interval'] is not None]
    sight = [r['interval'] for r in rows if r['kind'] == 'attraction' and r['interval'] is not None]
    meals = [r['interval'] for r in rows if r['kind'] in MEALS and r['interval'] is not None]
    busy = _union(traffic + sight + meals)
    known_m = sum(b - a for a, b in traffic)
    known_dist = sum(l['item']['distance'] for l in legs if _number(l['item'].get('distance')) and l['item']['distance'] >= 0)
    walking_dist = sum(l['item']['distance'] for l in legs if l['mode'] == 'walk' and _number(l['item'].get('distance')) and l['item']['distance'] >= 0)
    costs = [r['item'].get('cost') for r in rows if r['kind'] not in LOCAL] + [l['item'].get('cost') for l in legs]
    costs_known = flags['cost'] and all(_number(c) and c >= 0 for c in costs)
    flags['cost'] = costs_known
    day_rows = []
    for day in range(1, days + 1):
        span = [((day - 1) * 1440, day * 1440)]
        free = available[day] if available is not None else None
        available_m = _length(free) if free is not None else None
        busy_m = _length(_intersection(busy, free)) if free is not None and flags['timeline'] and flags['transport'] else None
        all_day_busy = _intersection(busy, span)
        intensity = [_field(r, resources, 'attractions', 'intensity') for r in rows if r['day'] == day and r['kind'] == 'attraction']
        intensity_known = all(_number(x) and x >= 0 for x in intensity)
        # Allocate leg distance over midnight in proportion to its explicit duration.
        day_walk_dist = 0.
        for leg in legs:
            if leg['mode'] != 'walk' or not _number(leg['item'].get('distance')):
                continue
            if leg['interval'] and leg['interval'][1] > leg['interval'][0]:
                day_walk_dist += leg['item']['distance'] * _length(_intersection([leg['interval']], span)) / (leg['interval'][1] - leg['interval'][0])
            elif leg['day'] == day:
                day_walk_dist += leg['item']['distance']
        day_rows.append({'day': day, 'available_intervals': free,
                         'available_minutes': available_m, 'busy_minutes': busy_m,
                         'busy_ratio': busy_m / available_m if busy_m is not None and available_m else None,
                         'idle_minutes': available_m - busy_m if busy_m is not None else None,
                         'outside_window_busy_minutes': _length(_subtract(all_day_busy, free)) if free is not None and flags['timeline'] and flags['transport'] else None,
                         'transport_minutes': _length(_intersection(traffic, span)) if flags['transport'] else None,
                         'walking_minutes': _length(_intersection(walk, span)) if flags['walking'] else None,
                         'walking_distance_km': day_walk_dist if flags['walking_distance'] and flags['walking'] else None,
                         'attraction_minutes': _length(_intersection(sight, span)) if flags['timeline'] else None,
                         'meal_minutes': _length(_intersection(meals, span)) if flags['timeline'] else None,
                         'activity_intensity': sum(intensity) if intensity_known else None})
    return {'days': day_rows, 'available_minutes': sum(_length(v) for v in available.values()) if available is not None else None,
            'transport_minutes': known_m if flags['transport'] else None, 'known_transport_minutes': known_m,
            'transport_distance_km': known_dist if flags['distance'] else None, 'known_transport_distance_km': known_dist,
            'walking_minutes': _length(walk) if flags['walking'] else None,
            'walking_distance_km': walking_dist if flags['walking_distance'] else None,
            'attraction_minutes': _length(sight) if flags['timeline'] else None,
            'meal_minutes': _length(meals) if flags['timeline'] else None,
            'total_cost': sum(costs) if costs_known else None,
            'known_cost': sum(c for c in costs if _number(c) and c >= 0),
            'activity_count': len(rows), 'transport_leg_count': len(legs),
            'unknown_transport_legs': sum(l['interval'] is None for l in legs)}


def _hotels(rows, resources, query, config, issues):
    hotels = []
    single_city = isinstance(query.get('target_city'), str) and bool(query['target_city'])
    for row in rows:
        if row['kind'] not in HOTELS:
            continue
        name = _identity(row['item'])
        city = _field(row, resources, 'hotels', 'city') or (query['target_city'] if single_city else None)
        nights = row['item'].get('nights')
        if nights is None and row['interval']:
            a, b = row['interval']
            nights = max(1, int(b // 1440) - int(a // 1440))
        if not isinstance(nights, int) or isinstance(nights, bool) or nights < 1:
            nights = None
        hotels.append(dict(row=row, name=name, city=city, nights=nights))
    switches, unknown = 0, any(h['name'] is None or h['nights'] is None for h in hotels)
    permitted = config['necessary_hotel_switches']
    for previous, current in zip(hotels, hotels[1:]):
        if previous['name'] == current['name']:
            continue
        if any(pair.get('from') == previous['name'] and pair.get('to') == current['name'] for pair in permitted):
            continue
        if not previous['city'] or not current['city']:
            unknown = True
        elif previous['city'] == current['city']:
            switches += 1
    return hotels, switches, unknown


def _visits(rows, config):
    visits, previous = [], None
    merged = 0
    unknown = False
    for row in rows:
        if row['kind'] == 'attraction':
            key = _identity(row['item'])
            if key is None:
                unknown = True
            contiguous = (previous is not None and previous['kind'] == 'attraction'
                          and key is not None and _identity(previous['item']) == key
                          and previous['interval'] is not None and row['interval'] is not None
                          and previous['interval'][1] == row['interval'][0])
            if contiguous:
                merged += 1
            else:
                visits.append(row)
        previous = row
    permitted = config['necessary_revisits']
    allowances = Counter(permitted) if isinstance(permitted, list) else Counter(permitted)
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in allowances.values()):
        raise ValueError('necessary_revisits must give nonnegative extra visit counts')
    seen, repeats = Counter(), 0
    for row in visits:
        name = _identity(row['item'])
        if seen[name] > allowances[name]:
            repeats += 1
        seen[name] += 1
    return visits, repeats, merged, unknown


def _semantic(entry):
    if not isinstance(entry, dict) or entry.get('status') != 'known':
        return _component(reason='semantic_unknown')
    rating = entry.get('rating')
    evidence = entry.get('evidence')
    if not isinstance(rating, int) or isinstance(rating, bool) or not 0 <= rating <= 4:
        return _component(reason='invalid_semantic_rating')
    if not isinstance(evidence, list) or not evidence or not all(isinstance(e, (str, dict)) and bool(e) for e in evidence):
        return _component(reason='missing_semantic_evidence')
    return _component(25. * rating, 'known', evidence=deepcopy(evidence), rating=rating)


def _mapping(value, mapping, thresholds):
    if value is None:
        return None
    if mapping == 'binary':
        if 'equals' not in thresholds:
            raise ValueError('binary requires equals')
        return float(value == thresholds['equals'])
    if not _number(value):
        return None
    if mapping in ('upper_bound', 'lower_bound'):
        target, scale = thresholds['target'], thresholds['scale']
        if not _number(target) or not _number(scale) or scale <= 0:
            raise ValueError('Bound requires finite target and positive scale')
        excess = value - target if mapping == 'upper_bound' else target - value
        return _clip(1 - max(0, excess) / scale)
    if mapping == 'interval':
        lo, hi = thresholds['lower'], thresholds['upper']
        ls, us = thresholds['lower_scale'], thresholds['upper_scale']
        if not all(_number(x) for x in (lo, hi, ls, us)) or lo > hi or min(ls, us) <= 0:
            raise ValueError('Invalid interval mapping')
        return _clip(1 - max(0, lo - value) / ls - max(0, value - hi) / us)
    raise ValueError('Unsupported mapping')


def _rule_preference(item, metrics, visits, hotels, resources, days):
    required = ('feature', 'mapping', 'thresholds', 'aggregation', 'missing_policy')
    if any(k not in item for k in required) or item['missing_policy'] != 'unknown' or not isinstance(item['thresholds'], dict):
        raise ValueError('Missing/unsupported frozen mapping configuration')
    feature, mapping, aggregation, thresholds = (item[k] for k in ('feature', 'mapping', 'aggregation', 'thresholds'))
    if mapping not in ('pace', 'tag_fraction') and feature != 'hotel_facilities':
        _mapping(0, mapping, thresholds)  # Validate configuration even if observations are empty.
    scope = _scope(item, days)
    selected_days = [d for d in metrics['days'] if d['day'] in scope]
    if feature == 'pace':
        if mapping != 'pace' or aggregation != 'available_minutes':
            raise ValueError('pace requires pace/available_minutes')
        lo, hi = thresholds['lower'], thresholds['upper']
        _pace(.5, lo, hi)
        values = [(d['busy_ratio'], d['available_minutes']) for d in selected_days]
        if any(w is None or (w > 0 and v is None) for v, w in values):
            return None
        denominator = sum(w for v, w in values)
        return sum(_pace(v, lo, hi) * w for v, w in values if w > 0) / denominator if denominator else None
    if feature == 'attraction_tags':
        if mapping != 'tag_fraction' or aggregation != 'unique_attractions':
            raise ValueError('attraction_tags requires tag_fraction/unique_attractions')
        wanted = thresholds.get('any_of')
        if not isinstance(wanted, list) or not wanted:
            raise ValueError('tag_fraction requires nonempty any_of')
        unique = {}
        for row in visits:
            if row['day'] in scope:
                key = _identity(row['item'])
                if key is None:
                    return None
                unique.setdefault(key, row)
        if not unique:
            return 0.
        matches = []
        for row in unique.values():
            tags = _field(row, resources, 'attractions', 'tags')
            if not isinstance(tags, list):
                return None
            matches.append(bool(set(tags).intersection(wanted)))
        return sum(matches) / len(matches)
    if feature in ('walking_minutes', 'walking_distance_km', 'activity_intensity'):
        if aggregation != 'day_equal':
            raise ValueError('Physical effort requires day_equal')
        values = [_mapping(d[feature], mapping, thresholds) for d in selected_days]
        return sum(values) / len(values) if values and all(v is not None for v in values) else None
    if feature.startswith('hotel_'):
        if aggregation != 'night_weighted':
            raise ValueError('Hotel preference requires night_weighted')
        field = {'hotel_stars': 'stars', 'hotel_price': 'price', 'hotel_facilities': 'facilities', 'hotel_city': 'city'}.get(feature)
        if field is None:
            raise ValueError('Unsupported hotel feature')
        values = []
        for hotel in hotels:
            if hotel['row']['day'] not in scope:
                continue
            value = hotel['city'] if field == 'city' else _field(hotel['row'], resources, 'hotels', field)
            if feature == 'hotel_facilities':
                targets = thresholds.get('all_of')
                if mapping != 'binary' or not isinstance(targets, list) or not targets:
                    raise ValueError('Facilities require binary and nonempty all_of')
                score = sum(t in value for t in targets) / len(targets) if isinstance(value, list) else None
            else:
                score = _mapping(value, mapping, thresholds)
            if score is None or hotel['nights'] is None:
                return None
            values.append((score, hotel['nights']))
        return sum(v * n for v, n in values) / sum(n for v, n in values) if values else 0.
    if aggregation != 'total' or feature not in metrics or isinstance(metrics[feature], (dict, list)):
        raise ValueError('Unsupported scalar feature/aggregation')
    return _mapping(metrics[feature], mapping, thresholds)


def _preferences(requirements, profile, semantic, metrics, visits, hotels, resources, days):
    if not isinstance(requirements, dict) or requirements.get('reviewed') is not True or not isinstance(requirements.get('items'), list):
        return None, 'unknown', [], {'known_weight': 0., 'required_weight': None, 'fraction': None}
    results, seen = [], set()
    payload = semantic.get('preferences', []) if isinstance(semantic, dict) else []
    semantic_rows = {r.get('id'): r for r in payload if isinstance(r, dict)} if isinstance(payload, list) else {}
    for item in requirements['items']:
        if isinstance(item, dict) and (item.get('kind') == 'hard' or item.get('applicable') is False):
            continue
        weight = item.get('weight', 1.) if isinstance(item, dict) else 1.
        row = _component(id=item.get('id') if isinstance(item, dict) else None, weight=weight, value=None)
        try:
            if not isinstance(item, dict) or item.get('kind') != 'soft' or not item.get('id') or item['id'] in seen:
                raise ValueError('Soft item needs unique id and kind=soft')
            seen.add(item['id'])
            if not _number(weight) or weight <= 0:
                raise ValueError('Preference weight must be positive')
            if not isinstance(item.get('evidence'), list) or not item['evidence']:
                raise ValueError('Source evidence is required')
            method = item.get('method')
            if method == 'semantic':
                scored = (_semantic(semantic_rows.get(item['id'])) if profile == 'full_v0' else
                          _component(reason='semantic_not_available'))
                row.update(scored)
                row['value'] = scored['score'] / 100 if scored['score'] is not None else None
            elif method == 'rule':
                value = _rule_preference(item, metrics, visits, hotels, resources, days)
                row.update(score=100 * value if value is not None else None, value=value,
                           status='known' if value is not None else 'unknown', reason=None if value is not None else 'missing_feature_data')
            else:
                raise ValueError('Unsupported preference method')
        except (KeyError, TypeError, ValueError) as exc:
            row.update(status='unconfigured', reason=str(exc), score=None, value=None)
        results.append(row)
    if not results:
        return None, 'not_applicable', [], {'known_weight': 0., 'required_weight': 0., 'fraction': None}
    valid_weights = all(_number(r['weight']) and r['weight'] > 0 for r in results)
    total = sum(r['weight'] for r in results) if valid_weights else None
    known = sum(r['weight'] for r in results if r['status'] == 'known') if valid_weights else 0.
    complete = total is not None and all(r['status'] == 'known' for r in results)
    score = sum(r['score'] * r['weight'] for r in results) / total if complete else None
    status = 'known' if complete else ('partial' if known > 0 else 'unknown')
    return score, status, results, {'known_weight': known, 'required_weight': total, 'fraction': known / total if total else None}


def score_plan(plan, query, *, profile='fast_rules_v0', requirements=None, config=None,
               semantic=None, resources=None):
    """Return JSON-safe metrics/components and Q/P/S; never mutate inputs."""
    if profile not in ('fast_rules_v0', 'full_v0'):
        raise ValueError('Unknown scoring profile')
    if not isinstance(plan, dict) or not isinstance(query, dict):
        raise TypeError('plan and query must be dictionaries')
    cfg, resources, issues = _config(config), resources or {}, []
    components = {key: _component() for key in ('T', 'R', 'H', 'U', 'E')}
    try:
        days = _day(query['days'])
    except (KeyError, TypeError, ValueError):
        days = 0
        issues.append({'code': 'unknown_requested_days'})
    rows, legs, flags = _parse(plan, resources, issues)
    available = _availability(rows, query, cfg, days, issues) if days else None
    metrics = _metrics(rows, legs, flags, available, days, resources, issues)
    hotels, switches, hotels_unknown = _hotels(rows, resources, query, cfg, issues)
    visits, repeats, merged, visits_unknown = _visits(rows, cfg)
    nights = sum(h['nights'] for h in hotels) if all(h['nights'] is not None for h in hotels) else None
    metrics.update(overnight_stays=nights, hotel_switches=switches if not hotels_unknown else None,
                   attraction_visits=len(visits), unique_attractions=len({_identity(r['item']) for r in visits}) if not visits_unknown else None,
                   nonessential_revisits=repeats if not visits_unknown else None, merged_visit_segments=merged)
    A, M = metrics['available_minutes'], metrics['transport_minutes']
    if A is not None and A > 0 and M is not None:
        components['T'] = _component(100 * _clip(1 - M / A), 'known', denominator=A, numerator=M)
    else:
        components['T'] = _component(reason='zero_or_unknown_availability' if not A else 'unknown_transport')
    pace_targets = {d: cfg['pace_target'] for d in range(1, days + 1)}
    pace_error = False
    assigned_targets = {}
    if isinstance(requirements, dict) and requirements.get('reviewed') is True and isinstance(requirements.get('items'), list):
        for item in requirements.get('items', []):
            if isinstance(item, dict) and item.get('kind') == 'soft' and item.get('feature') == 'pace' and item.get('applicable') is not False:
                try:
                    # Validate all protocol fields before using a preference to alter R.
                    if item.get('method') != 'rule':
                        raise ValueError('Pace target must be frozen as a rule')
                    _rule_preference(item, metrics, visits, hotels, resources, days)
                    target = [item['thresholds']['lower'], item['thresholds']['upper']]
                    for d in _scope(item, days):
                        if d in assigned_targets and assigned_targets[d] != target:
                            raise ValueError('Conflicting frozen pace targets on the same day')
                        assigned_targets[d] = target
                        pace_targets[d] = target
                except (KeyError, TypeError, ValueError) as exc:
                    pace_error = True
                    issues.append({'code': 'unconfigured_pace_target', 'reason': str(exc)})
    if A is not None and A > 0 and not pace_error and all(d['busy_ratio'] is not None for d in metrics['days'] if d['available_minutes']):
        score = sum(_pace(d['busy_ratio'], *pace_targets[d['day']]) * d['available_minutes'] for d in metrics['days'] if d['available_minutes']) / A
        components['R'] = _component(100 * score, 'known', denominator=A, targets=pace_targets)
    else:
        components['R'] = _component(reason='unknown_busy_or_available_time')
    required_nights = (0 if cfg['applicability']['H'] is False else cfg['required_nights']
                       if cfg['required_nights'] is not None else max(0, days - 1) if days else None)
    if hotels_unknown:
        components['H'] = _component(reason='unknown_hotel_identity_city_or_nights')
    elif nights:
        components['H'] = _component(100 * _clip(1 - switches / nights), 'known', numerator=switches, denominator=nights)
    elif required_nights:
        components['H'] = _component(0., 'known', reason='missing_required_stay', numerator=0, denominator=0)
        issues.append({'code': 'missing_required_stay'})
    components['U'] = (_component(reason='unknown_attraction_identity') if visits_unknown else
                       _component(100 * (1 - repeats / len(visits)) if visits else 0., 'known',
                                  reason=None if visits else 'empty_attractions', numerator=repeats, denominator=len(visits)))
    if not visits and cfg['applicability']['U']:
        issues.append({'code': 'empty_attractions'})
    if profile == 'full_v0' and isinstance(semantic, dict):
        components['E'] = _semantic(semantic.get('experience'))
    else:
        components['E'] = _component(reason='semantic_not_available')
    applicability = deepcopy(cfg['applicability'])
    if applicability['H'] is None:
        applicability['H'] = required_nights > 0 if required_nights is not None else True
    if profile == 'fast_rules_v0':
        applicability['E'] = False
    active = []
    for name, component in components.items():
        component['weight'] = cfg['quality_weights'][name]
        component['applicable'] = applicability[name]
        if not applicability[name]:
            component.update(score=None, status='not_applicable', reason='excluded_by_profile' if name == 'E' and profile == 'fast_rules_v0' else 'frozen_task_applicability')
        elif cfg['quality_weights'][name] > 0:
            active.append(name)
    total_weight = sum(cfg['quality_weights'][k] for k in active)
    known_weight = sum(cfg['quality_weights'][k] for k in active if components[k]['status'] == 'known')
    quality_status = 'not_applicable' if not total_weight else 'known' if all(components[k]['status'] == 'known' for k in active) else 'unknown'
    quality = sum(cfg['quality_weights'][k] * components[k]['score'] for k in active) / total_weight if quality_status == 'known' else None
    preference, preference_status, preferences, preference_coverage = _preferences(requirements, profile, semantic, metrics, visits, hotels, resources, days)
    composite = (quality if preference_status == 'not_applicable' else .5 * (quality + preference)) if quality is not None and preference_status in ('known', 'not_applicable') else None
    return {'profile': profile, 'quality_score': quality, 'preference_score': preference, 'composite_score': composite,
            'quality_status': quality_status, 'preference_status': preference_status,
            'composite_status': 'known' if composite is not None else 'not_applicable' if quality_status == preference_status == 'not_applicable' else 'unknown',
            'components': components, 'preferences': preferences, 'metrics': metrics,
            'coverage': {'quality': {'known_weight': known_weight, 'required_weight': total_weight,
                                     'fraction': known_weight / total_weight if total_weight else None},
                         'preferences': preference_coverage, 'data': flags},
            'issues': issues}
