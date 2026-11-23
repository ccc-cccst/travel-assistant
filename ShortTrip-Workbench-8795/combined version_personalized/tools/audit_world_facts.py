"""Inventory the installed Chinese planning snapshot without model or network calls."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KINDS = {"attraction": "type", "restaurant": "cuisine", "accommodation": "featurehoteltype"}
CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d|24:00")


def money(value):
    try:
        return math.isfinite(float(value)) and float(value) >= 0
    except (TypeError, ValueError):
        return False


def time_valid(value):
    return bool(CLOCK.fullmatch(str(value)))


def catalog_audit(sandbox, city, kind, tag):
    page = sandbox.catalog_list(kind, city, limit=100)
    records = list(page['records'])
    offset = page['next_offset']
    while offset is not None:
        following = sandbox.catalog_list(kind, city, limit=100, offset=offset)
        records.extend(following['records'])
        offset = following['next_offset']
    counts = Counter()
    names = Counter(r['name'] for r in records)
    ids = Counter(r['record_id'] for r in records)
    for record in records:
        payload = record['payload']
        data, access = payload['data'], payload['access']
        counts['missing_tag'] += not bool(data.get(tag))
        counts['valid_numeric_price'] += money(data.get('price'))
        counts['zero_numeric_price'] += money(data.get('price')) and float(data['price']) == 0
        counts['historical_conditional_price'] += bool(access.get('historical_price_context'))
        counts['estimated_price_reference'] += money(access.get('estimated_price_reference'))
        counts['price_assumption'] += access.get('price_assumption') not in (None, 'not_applied_before_actual_search')
        if kind == 'accommodation':
            counts['missing_bed_count'] += not bool(data.get('numbed'))
        else:
            start, end = data.get('opentime'), data.get('endtime')
            valid = time_valid(start) and time_valid(end)
            counts['clock_pair_present'] += valid
            counts['same_clock_pair'] += valid and start == end
            counts['overnight_clock_pair'] += valid and end < start
            counts['segmented_hours'] += bool(data.get('opening_segments_json'))
    return {
        **{k: page[k] for k in ('source_row_count', 'aliased_rows', 'unresolved_rows', 'catalog_total', 'planning_catalog_total')},
        **counts,
        'same_name_groups': sum(n > 1 for n in names.values()),
        'duplicate_record_id_groups': sum(n > 1 for n in ids.values()),
    }


def transport_audit(env, sandbox):
    # One selection per city pair and mode, including rows missing a fare.
    results = []
    for start in sandbox.cities:
        for end in sandbox.cities:
            if start == end:
                continue
            for mode in ('train', 'airplane'):
                frame = env.intercitytransport._select(start, end, mode)
                rows = frame.to_dict('records')
                eligible = sum(
                    money(r.get('Cost')) and money(r.get('Duration')) and float(r['Duration']) > 0
                    and time_valid(r.get('BeginTime')) and time_valid(r.get('EndTime'))
                    for r in rows
                )
                results.append({'origin': start, 'destination': end, 'mode': mode, 'static_rows': len(rows),
                                'valid_price_and_clock': eligible})
    return results


def main():
    from chinatravel.personalization.cli import DATA, environment

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('use a new output path')
    env = environment(args.data)
    from chinatravel.environment.planning_access import _sandbox

    sandbox = _sandbox()
    by_city = {city: {kind: catalog_audit(sandbox, city, kind, tag) for kind, tag in KINDS.items()}
               for city in sandbox.cities}
    by_kind = {kind: dict(sum((Counter(city[kind]) for city in by_city.values()), Counter())) for kind in KINDS}
    dated = sandbox.intercity_search('北京', '厦门', 'airplane', travel_date='2026-11-20') if '厦门' in by_city else {}
    result = {
        'installation': json.loads((args.data / 'install.json').read_text(encoding='utf-8')),
        'cities': by_city, 'totals': by_kind, 'intercity': transport_audit(env, sandbox),
        'dated_transport_probe': {k: dated[k] for k in ('returned', 'dated_observations', 'errors') if k in dated},
        'route_probe': sandbox.goto('上海', '外滩', '豫园', 'taxi', '10:00'),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
    print(json.dumps({'audit': str(args.out), 'cities': len(by_city), 'totals': by_kind}, ensure_ascii=False))


if __name__ == '__main__':
    main()
