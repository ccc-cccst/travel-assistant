"""Read-only travel facts with explicit provenance for the configured snapshot."""

from copy import deepcopy
from datetime import date, timedelta
from functools import lru_cache
from hashlib import sha256
import inspect
import json
import math
from pathlib import Path
import re

from .reference_scope import HISTORICAL_RAIL_FARE, reference_cities, reference_route


KINDS = {
    'attraction': ('type', 'category_values', 'per_person'),
    'restaurant': ('cuisine', 'cuisine_values', 'per_person_per_meal'),
    'accommodation': ('featurehoteltype', None, 'per_room_per_night'),
}
CLOCK = re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d|24:00')
TICKET_OPTIONS = {
    '飞机': ('airplane', None, None), '火车': ('train', None, None),
    '高铁': ('train', '高铁', None), '高铁G': ('train', '高铁', 'G'),
    '动车': ('train', '动车', None), '动车D': ('train', '动车', 'D'),
}


def ticket_option(option, mode, data):
    expected_mode, train_type, prefix = TICKET_OPTIONS[option]
    return (mode == expected_mode and (train_type is None or data.get('TrainType') == train_type)
            and (prefix is None or bool(re.fullmatch(rf'{prefix}\d+', str(data.get('TrainID', ''))))))


def numeric(value, *, minimum=0):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= minimum else None


def day(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('日期须为 YYYY-MM-DD')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('日期须为 YYYY-MM-DD')
    return parsed


def clock_minutes(value):
    if not isinstance(value, str) or not CLOCK.fullmatch(value):
        return None
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def positive_integer(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f'{label}须为正整数')
    return value


def strings(value, label):
    if not isinstance(value, (tuple, list)) or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValueError(f'{label}须为非空文本组成的列表')
    return tuple(dict.fromkeys(x.strip() for x in value))


def opening(data, access, kind):
    if kind == 'accommodation':
        return {'status': 'not_applicable', 'windows_minutes': [], 'verified_for_date': False}
    opening_clock, closing_clock = data.get('opentime'), data.get('endtime')
    windows, status = [], 'unknown'
    segments = data.get('opening_segments_json')
    if segments:
        try:
            values = json.loads(segments) if isinstance(segments, str) else segments
            if isinstance(values, list) and values and all(
                isinstance(pair, (list, tuple)) and len(pair) == 2
                and all(type(t) is int for t in pair) and 0 <= pair[0] < pair[1] <= 1440 for pair in values
            ):
                windows, status = values, 'historical_reference'
        except (ValueError, TypeError):
            pass
    elif access.get('time_complete'):
        start, end = clock_minutes(opening_clock), clock_minutes(closing_clock)
        if start is not None and end is not None and start < 1440 and start != end:
            windows, status = [[start, end if end > start else end + 1440]], 'snapshot_reference'
    return {'status': status, 'windows_minutes': windows, 'verified_for_date': False,
            'reported_clock': {'open': opening_clock, 'close': closing_clock},
            'source_fields': ['opening_segments_json'] if segments else ['opentime', 'endtime'],
            'observed_at': data.get('opening_segments_observed_at'),
            'source_sha256': data.get('opening_segments_source_sha256')}


def price(data, access, unit):
    value = numeric(data.get('price'))
    status = 'snapshot_reference' if value is not None else 'unknown'
    context = access.get('historical_price_context') or access.get('price_evidence')
    if access.get('historical_price_context'):
        status, unit = 'historical_reference', None
    elif access.get('price_evidence'):
        assumption = str(access.get('price_assumption', ''))
        status = 'assumption' if ('assum' in assumption.lower() or 'assum' in str(context).lower()) else 'source_reference'
    elif value is None and numeric(access.get('estimated_price_reference')) is not None:
        value, status, unit = numeric(access['estimated_price_reference']), 'estimate', None
    return {'value': value, 'currency': 'CNY', 'unit': unit, 'status': status,
            'unit_basis': 'benchmark_convention' if unit else None,
            'source_status': access.get('price_source_status'), 'source_context': context,
            'usable_for_snapshot_budget': status == 'snapshot_reference' and unit is not None,
            'verified_for_date': False, 'inventory': 'unknown'}


def planning_place(place):
    """Apply the V20 reference lodging convention only during itinerary calculation."""
    if (place['kind'] != 'accommodation' or place['city'] != '厦门' or place['city'] not in reference_cities()
            or not place['planning_catalog'] or not place['routable_in_snapshot'] or not place['benchmark_bed_count']):
        return place
    fare = place['price']
    source = fare.get('source_context') or {}
    if (fare['status'] != 'historical_reference' or not fare['value']
            or not source.get('historical_price_evidence_id')):
        return place
    return {**place, 'price': {**fare, 'unit': 'per_room_per_night',
            'unit_basis': 'historical_room_price_proxy', 'usable_for_snapshot_budget': True}}


class WorldFacts:
    """One instance per verified installation and process, with bounded query caches."""

    def __init__(self, sandbox, installation):
        self.sandbox = sandbox
        self.installation = Path(installation).resolve()
        install = json.loads((self.installation / 'install.json').read_text(encoding='utf-8-sig'))
        source = install.get('source', install)
        self.version = {'commit': source['commit'], 'profile': source['profile'],
                        'source': 'installed_snapshot', 'live_lookup': False}
        self._route_accepts_party = 'people_number' in inspect.signature(sandbox.goto).parameters

    @classmethod
    def configured(cls, installation):
        # The standard CLI/web entry verifies and activates the profile before this import.
        from chinatravel.environment.planning_access import _sandbox

        return cls(_sandbox(), installation)

    def _city(self, city):
        if not isinstance(city, str) or city not in self.sandbox.cities:
            raise ValueError('目的地或出发地不在当前数据范围内')

    def _source(self, path, digest=None):
        path = Path(path).resolve()
        try:
            name = path.relative_to(self.installation).as_posix()
        except ValueError:
            name = path.name
        return {'file': name, 'sha256': digest or self._hash(path), 'commit': self.version['commit']}

    @staticmethod
    @lru_cache(maxsize=512)
    def _hash(path):
        return sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    @lru_cache(maxsize=36)
    def _catalog(self, city, kind):
        offset, rows = 0, []
        while offset is not None:
            page = self.sandbox.catalog_list(kind, city, limit=100, offset=offset)
            rows.extend(page['records'])
            offset = page['next_offset']
        return tuple(rows), page.get('planning_catalog_total', page['catalog_total'])

    def _place(self, record):
        payload = record['payload']
        data, access = payload['data'], payload['access']
        kind = record['kind']
        tag, normalized, unit = KINDS[kind]
        tags = data.get(normalized) if normalized else None
        locator = payload['source_locator']
        source = self._source(locator['file'], locator['file_sha256'])
        source['row_index_0based'] = locator['row_index_0based']
        source['poi_file_sha256'] = locator.get('poi_file_sha256')
        supplemental_sources = {key: deepcopy(access[key]) for key in ('dynamic_field_evidence', 'public_area_hours_evidence')
                                if access.get(key)}
        normalized_source = access.get('catalog_completion', {}).get('evidence')
        if normalized_source:
            supplemental_sources['catalog_normalization'] = normalized_source
        lat, lon = numeric(data.get('lat'), minimum=-90), numeric(data.get('lon'), minimum=-180)
        routable = bool(access.get('planning_position_available') and lat is not None and lon is not None
                        and lat <= 90 and lon <= 180 and (lat, lon) != (0, 0))
        duration = numeric(data.get('recommendmintime')), numeric(data.get('recommendmaxtime'))
        tag_values = tags if tags is not None else ([data[tag]] if data.get(tag) else [])
        if kind == 'accommodation' and isinstance(data.get(tag), str):
            tag_values = [part.strip() for part in re.split(r'[|,，;；、]', data[tag]) if part.strip()]
        return {
            'id': record['record_id'], 'kind': kind, 'name': record['name'], 'city': record['city'],
            'aliases': payload.get('aliases') or [], 'source': source, 'supplemental_sources': supplemental_sources,
            'location': {'lat': lat, 'lon': lon, 'basis': access.get('position_basis', 'snapshot_reference')}
            if routable else None,
            'routable_in_snapshot': routable, 'planning_catalog': bool(access['main_path']),
            'tags': {'values': tag_values, 'source_field': tag, 'raw': data.get(tag)},
            'opening': opening(data, access, kind), 'price': price(data, access, unit),
            'recommended_stay_hours': list(duration) if all(v is not None and v > 0 for v in duration)
            and duration[0] <= duration[1] else None,
            'benchmark_bed_count': numeric(data.get('numbed'), minimum=1) if kind == 'accommodation' else None,
            'missing_fields': access.get('planning_required_missing', []),
        }

    @staticmethod
    def _names(record):
        return [record['name'], *(a if isinstance(a, str) else a.get('name', '')
                                 for a in record['payload'].get('aliases', []))]

    def attraction_choices(self, city, query, limit=24):
        self._city(city)
        if not isinstance(query, str) or len(query) > 80:
            raise ValueError('请输入 80 字以内的景点名称。')
        needle = query.strip().casefold()
        rows, _ = self._catalog(city, 'attraction')
        if needle:
            rows = [r for r in rows if any(needle in name.casefold() for name in self._names(r) if name)]
            rows.sort(key=lambda r: (r['name'].casefold() != needle, not r['name'].casefold().startswith(needle)))
        result = []
        for row in rows:
            place = self._place(row)
            if not (place['planning_catalog'] and place['routable_in_snapshot'] and place['location']):
                continue
            result.append({key: place[key] for key in ('id', 'name', 'location')})
            if len(result) >= limit:
                break
        return result

    def _matches(self, city, text, kind=None):
        needle = text.strip().casefold()
        return [r for k in ([kind] if kind else KINDS) for r in self._catalog(city, k)[0]
                if needle == r['record_id'].casefold()
                or any(needle == name.casefold() for name in self._names(r) if name)]

    def place(self, city, text, kind=None, *, dining_details=False):
        self._city(city)
        if not isinstance(text, str) or not text.strip() or (kind is not None and
                                                            (not isinstance(kind, str) or kind not in KINDS)):
            raise ValueError('需要地点名称或 ID，以及有效的地点类型')
        rows = self._matches(city, text, kind)
        state = 'found' if len(rows) == 1 else 'ambiguous' if rows else 'no_catalog_match'
        if not rows:
            parts = text.strip().split(':')
            if len(parts) == 3 and parts[0] in KINDS and parts[1] == self.sandbox.cities[city] and (kind in (None, parts[0])):
                try:
                    reference = self.sandbox.catalog_entity(text.strip())
                except KeyError:
                    pass
                else:
                    if reference.get('status') == 'reference_only':
                        return {'status': 'reference_only', 'query': text, 'records': [], 'reference': reference}
        records = [self._place(r) for r in rows]
        if dining_details and state=='found' and kind=='restaurant':
            dishes = rows[0]['payload']['data'].get('recommendedfood')
            if isinstance(dishes, str):
                records[0]['reference_dishes'] = [s.strip() for s in re.split(r'[,，、|;；]', dishes)
                                                   if s.strip().casefold() not in ('','未知','暂无','nan','none','null')][:3]
        return {'status': state, 'query': text, 'records': records}

    def place_with_suggestions(self, city, text, kind=None):
        result = self.place(city, text, kind)
        if result['status'] != 'no_catalog_match':
            return result
        needle = text.strip().casefold()
        matches = [r for k in ([kind] if kind else KINDS) for r in self._catalog(city, k)[0]
                   if any(needle in name.casefold() for name in self._names(r) if name)]
        if matches:
            result.update(status='ambiguous' if len(matches) > 1 else 'unconfirmed_match',
                          match_basis='name_or_alias_contains_text', matched_count=len(matches),
                          records=[self._place(row) for row in matches[:12]])
        return result

    @lru_cache(maxsize=128)
    def _intercity(self, origin, destination, mode, travel_date, seat_code):
        return self.sandbox.intercity_search(origin, destination, mode, travel_date=travel_date, seat_code=seat_code)

    def intercity(self, origin, destination, mode='any', travel_date=None, seat_code=None, limit=12,
                  service=None, restrictions=(), *, include_reference_fares=False):
        self._city(origin)
        self._city(destination)
        day(travel_date)
        limit = min(positive_integer(limit, '返回条数'), 100)
        if origin == destination or mode not in ('any', 'train', 'airplane'):
            raise ValueError('需要不同城市或有效的城际交通方式')
        if seat_code is not None and (not isinstance(seat_code, str) or not seat_code.strip()):
            raise ValueError('席别须为非空文本')
        if service is not None and (not isinstance(service, str) or not service.strip()):
            raise ValueError('车次或航班号须为非空文本')
        if not isinstance(restrictions, (tuple, list)) or any(
            not isinstance(rule, (tuple, list)) or len(rule) != 2 or rule[0] not in ('include', 'exclude')
            or not isinstance(rule[1], (tuple, list)) or not rule[1]
            or any(not isinstance(option, str) or option not in TICKET_OPTIONS for option in rule[1])
            for rule in restrictions
        ):
            raise ValueError('班次筛选须包含有效的方式或车次类型')
        if type(include_reference_fares) is not bool:
            raise ValueError('是否使用历史参考票价须为布尔值')
        result = self._intercity(origin, destination, mode, travel_date, seat_code)
        use_reference = include_reference_fares and reference_route(origin, destination) and seat_code is None
        transport = list(result['records'])
        if use_reference and travel_date and mode != 'airplane':
            def service_key(item):
                data = item['record']['payload']['data']
                return tuple(data.get(key) for key in ('TrainID', 'From', 'To', 'BeginTime', 'EndTime'))

            scheduled = {service_key(item) for item in transport if item['mode'] == 'train'}
            undated = self._intercity(origin, destination, 'train', None, None)
            for item in undated['records']:
                data = item['record']['payload']['data']
                key = service_key(item)
                if key not in scheduled and data.get('CostSource') == HISTORICAL_RAIL_FARE:
                    transport.append(item)
                    scheduled.add(key)
        snapshot_rows = [item for item in transport if service is None or item['record']['name'] == service]
        before_restrictions = len(snapshot_rows)
        if restrictions:
            snapshot_rows = [item for item in snapshot_rows if all(
                any(ticket_option(option, item['mode'], item['record']['payload']['data']) for option in options)
                == (operator == 'include') for operator, options in restrictions
            )]
        dated_rows = result.get('dated_observations', [])
        conditional_rows = result.get('conditional_records', [])
        references = result.get('reference_records', [])
        if service is not None:
            def matches_service(row):
                return service in (row.get('service'), row.get('name'), row.get('flight_no'), row.get('train_no'))

            dated_rows = [row for row in dated_rows if matches_service(row)]
            conditional_rows = [row for row in conditional_rows if matches_service(row)]
            references = [row for row in references if matches_service(row)]
        records = []
        db = self.sandbox.db / 'intercity_transport'
        sources = {
            'train': self._source(db / 'train' / f'from_{origin}_to_{destination}.json'),
            'airplane': self._source(db / 'airplane.jsonl'),
        }
        for item in snapshot_rows[:limit]:
            native = item['record']
            payload = native['payload']
            data, kind = payload['data'], item['mode']
            source = dict(sources[kind])
            source['record_key'] = {k: data.get(k) for k in ('TrainID', 'FlightID', 'From', 'To', 'BeginTime') if k in data}
            freshness = payload.get('freshness_status', 'static_benchmark_snapshot')
            published = data.get('CostSource')
            historical = use_reference and published == HISTORICAL_RAIL_FARE and numeric(data.get('Cost')) is not None
            observations = {k: v for k, v in data.items() if k.startswith(('Cost', 'Official', 'PublishedEvidence', 'Display'))
                            and k != 'Cost' and v is not None}
            observations.update({k: payload[k] for k in ('dynamic_train_evidence', 'dynamic_price_evidence') if k in payload})
            duration_hours = numeric(data.get('Duration'))
            records.append({'id': f'{origin}:{destination}:{native["record_id"]}', 'mode': kind, 'service': native['name'],
                            'train_type': data.get('TrainType') if kind == 'train' else None,
                            'from': data.get('From'), 'to': data.get('To'), 'departure': data.get('BeginTime'),
                            'arrival': data.get('EndTime'), 'source_duration_hours': duration_hours,
                            'duration_minutes': round(60 * duration_hours) if duration_hours is not None else None,
                            'fare': {'value': numeric(data.get('Cost')), 'unit': 'CNY/person', 'source_status': published,
                                     'usable_for_snapshot_budget': historical or (
                                         bool(payload.get('static_budget_eligible')) and freshness == 'static_benchmark_snapshot'),
                                     'verified_for_date': False},
                            'status': freshness, 'availability': 'unknown', 'source': source,
                            'extra_evidence': observations})
        dated = [{**r, 'availability_at_observation': r.get('availability'), 'availability': 'unknown',
                  'verified_for_date': False, 'strict_budget_eligible': False}
                 for r in deepcopy(dated_rows[:limit])]
        counts = {'snapshot_records': len(snapshot_rows), 'dated_observations': len(dated_rows),
                  'conditional_records': len(conditional_rows), 'other_references': len(references)}
        if restrictions:
            counts['snapshot_records_before_restrictions'] = before_restrictions
        return {'origin': origin, 'destination': destination, 'requested_date': travel_date,
                'status': 'partial_or_query_error' if result['errors'] else 'evidence_found' if any(counts.values())
                else 'no_records_in_snapshot', 'counts': counts, 'records': records, 'dated_observations': dated,
                'conditional_records': [{**r, 'source': sources.get(r.get('mode')), 'availability': 'unknown',
                                         'strict_budget_eligible': False}
                                        for r in deepcopy(conditional_rows[:limit])],
                'other_references': deepcopy(references[:limit]),
                'errors': list(result['errors']), 'current_availability': 'unknown'}

    @lru_cache(maxsize=512)
    def _route(self, city, start, end, mode, departure, people):
        if self._route_accepts_party:
            return self.sandbox.goto(city, start, end, mode, departure, people_number=people)
        result = deepcopy(self.sandbox.goto(city, start, end, mode, departure))
        for leg in result['legs']:
            leg['price'] = leg['cost']
            if leg['mode'] == 'taxi':
                leg['cars'] = (people + 3) // 4
                leg['cost'] *= leg['cars']
            elif leg['mode'] == 'metro':
                leg['tickets'] = people
                leg['cost'] *= people
        return {**result, 'people_number': people}

    def route(self, city, start, end, mode, departure, people=1, travel_date=None):
        self._city(city)
        day(travel_date)
        positive_integer(people, '人数')
        endpoints = strings((start, end), '起终点')
        time = clock_minutes(departure)
        if len(endpoints) != 2 or mode not in ('walk', 'metro', 'taxi') or time is None or time == 1440:
            raise ValueError('需要不同的起终点、有效的市内交通方式和 HH:MM 出发时间')
        resolved = []
        for endpoint in endpoints:
            matches = self._matches(city, endpoint)
            if len(matches) > 1:
                return {'status': 'ambiguous_endpoint', 'endpoint': endpoint,
                        'options': [r['record_id'] for r in matches], 'legs': [], 'available': False}
            key = 'record_id' if self._route_accepts_party else 'name'
            resolved.append(matches[0][key] if matches else endpoint)
        try:
            result = deepcopy(self._route(city, *resolved, mode, departure, people))
        except (TypeError, KeyError, ValueError, RuntimeError, ImportError) as error:
            result = {'available': False, 'legs': [], 'error': str(error), 'query_error': type(error).__name__}
        if not result['available'] and re.match(r'\w+(?:Error|Exception):', str(result.get('error'))):
            result['query_error'] = result['error'].split(':', 1)[0]
        duration, cost = None, None
        if result['available']:
            final_clock = result['legs'][-1].get('end_time', '')
            if isinstance(final_clock, str) and re.fullmatch(r'\d{2,}:[0-5]\d', final_clock):
                h, m = map(int, final_clock.split(':'))
                duration = h * 60 + m - time
            cost = round(sum(leg['cost'] for leg in result['legs']), 2)
        result.update(status='estimated' if result['available'] else 'route_query_error' if result.get('query_error')
                      else 'route_unavailable_in_snapshot',
                      requested_date=travel_date, verified_for_date=False,
                      duration_minutes=duration, party_cost_cny=cost,
                      provenance=[self._source(self.sandbox.db / 'transportation/subways.json'),
                                  self._source(self.sandbox.db / 'poi' / self.sandbox.cities[city] / 'poi.json')])
        return result

    def snapshot(self, destination, origin=None, start_date=None, days=None, pins=(), terms=(), modes=('any',), limit=12):
        self._city(destination)
        if origin is not None:
            self._city(origin)
        start = day(start_date)
        if days is not None:
            positive_integer(days, '天数')
        limit = min(positive_integer(limit, '返回条数'), 100)
        pins, terms, modes = strings(pins, '指定地点'), strings(terms, '候选关键词'), strings(modes, '城际方式')
        if not modes or any(m not in ('any', 'train', 'airplane') for m in modes):
            raise ValueError('需要有效的城际交通方式')
        counts, candidates = {}, {}
        for kind in KINDS:
            items, trial = self._catalog(destination, kind)
            if terms:
                tag, normalized, _ = KINDS[kind]
                def matches(row):
                    data = row['payload']['data']
                    text = ' '.join([row['name'], str(data.get(tag, '')), str(data.get(normalized, ''))]).casefold()
                    return any(term.casefold() in text for term in terms)
                selected = [r for r in items if matches(r)]
            else:
                selected = items
            candidates[kind] = [self._place(r) for r in selected[:limit]]
            counts[kind] = {'discoverable': len(items), 'planning_catalog': trial,
                            'matched': len(selected), 'returned': len(candidates[kind])}
        transport = {}
        if origin and origin != destination:
            finish = (start + timedelta(days=days - 1)).isoformat() if start and days else None
            chosen_modes = ('any',) if 'any' in modes else modes
            transport['outbound'] = [self.intercity(origin, destination, m, start_date, limit=limit) for m in chosen_modes]
            transport['return'] = [self.intercity(destination, origin, m, finish, limit=limit) for m in chosen_modes]
        return {'schema_version': 'world_snapshot_v1', 'data': dict(self.version),
                'destination': destination, 'origin': origin, 'requested_start_date': start_date,
                'pinned_places': [self.place(destination, p) for p in pins], 'candidates': candidates,
                'coverage': counts, 'intercity': transport}

    def query(self, value):
        if not isinstance(value, dict):
            raise ValueError('查询须为 JSON 对象')
        if value.get('schema_version') == 'tripspec_v1':
            return self.trip_snapshot(value)
        action = value.get('action', 'snapshot')
        handlers = {'snapshot': self.snapshot, 'trip_snapshot': self.trip_snapshot,
                    'place': self.place, 'intercity': self.intercity, 'route': self.route}
        if not isinstance(action, str) or action not in handlers:
            raise ValueError('action 须为 snapshot、trip_snapshot、place、intercity 或 route')
        handler = handlers[action]
        params = {key: val for key, val in value.items() if key != 'action'}
        try:
            inspect.signature(handler).bind(**params)
        except TypeError as error:
            raise ValueError(f'查询参数有误：{error}') from error
        result = handler(**params)
        return result if action in ('snapshot', 'trip_snapshot') else {'data': dict(self.version), **result}

    def trip_snapshot(self, spec, limit=12):
        from .trip_world import snapshot_for_trip

        return snapshot_for_trip(self, spec, limit)
