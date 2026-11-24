"""Opt-in, once-per-trip semantic recall over the complete snapshot catalog.

The model proposes directions, never facts or a feasible itinerary. Stable IDs
are checked against the active planner table; local recall remains a fallback.
"""
import json
import math

VERSION = 'trip_recall_v1'


def read_directions(answer, allowed):
    try:
        value, _ = json.JSONDecoder().raw_decode(answer[answer.index('{'):])
    except (ValueError, TypeError, AttributeError):
        return []
    groups = value.get('directions') if isinstance(value, dict) else None
    if not isinstance(groups, list):
        return []
    result = []
    for group in groups[:6]:
        ids = group.get('attractions') if isinstance(group, dict) else None
        # Reject a whole invented/malformed group rather than silently repairing it.
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in allowed for i in ids):
            continue
        ids = list(dict.fromkeys(ids))[:6]
        if not any(set(ids) == set(g['attractions']) for g in result):
            result.append({'label': str(group.get('label', ''))[:80], 'attractions': ids})
    return result


def context(request, rows, catalog):
    cards = []
    for i, row in rows.iterrows():
        name = str(row['name'])
        point = catalog.env.poi.search(catalog.city, name)
        location = None
        if isinstance(point, (list, tuple)) and len(point) == 2:
            if all(isinstance(v, (float, int)) and math.isfinite(v) for v in point):
                location = [round(float(v), 4) for v in point]
        cards.append([int(i), name, str(row['type']), str(row['opentime']),
                      str(row['endtime']), float(row['price']), location,
                      catalog.attributes('attraction', name)['tags']])
    # Public confirmed facts only; no benchmark answer/preference oracle.
    return {'version': VERSION, 'trip': request['trip'], 'raw_text': request['raw_text'],
            'facts': request['facts'], 'preferences': request['preferences'],
            'appointments': request['locked_events'], 'hard_limits': request['hard_limits'],
            'columns': ['id', 'name', 'type', 'opens', 'closes', 'reference_price', 'lat_lon', 'tags'],
            'scope': 'complete_active_city_catalog; snapshot_not_live; no_hotel_or_tickets_chosen',
            'attractions': cards}


def propose(model, payload):
    prompt = (
        '为整趟旅行召回值得试排的景点方向，现在尚未选定交通班次和酒店。'
        '通读全城候选和用户已确认需求，提出最多六组互有差异的区域或体验主题，'
        '每组二至六个可搭配的景点。优先覆盖明确兴趣与必去地点；没有明确兴趣时提供'
        '有不同体验的城市游览方向，避免只围绕车站机场。参考坐标保持组内适度集中，'
        '保留跨区选择。预约是硬约束，但此处不排日期、顺序、具体时间，也不声称可行。'
        '只用输入ID，不添加地点或虚构评分。只输出短JSON：'
        '{"directions":[{"label":"区域或主题","attractions":[1,2,3]}]}。\n'
        + json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    return model([{'role': 'user', 'content': prompt}], one_line=False, max_tokens=900)


def blend(rows, baseline, ordered, required, directions, catalog, start, end, limit):
    """Reserve half the daily slots for semantic directions; keep local options.

    Geometry only chooses among model directions. It cannot delete city records,
    establish feasibility, or overrule pinned places. The caller removes visited
    places and appointments belonging to other days before passing `ordered`.
    """
    allowed = set(ordered)
    result = [int(i) for i in ordered if i in set(required)]
    groups = []
    for rank, direction in enumerate(directions):
        ids = [i for i in direction['attractions'] if i in allowed and i not in result]
        if not ids:
            continue
        distances = []
        for i in ids:
            name = rows.iloc[i]['name']
            a, b = catalog.distance(start, name), catalog.distance(name, end)
            distances.append(a + b if a is not None and b is not None else float('inf'))
        groups.append((sum(distances) / len(distances), rank, ids))
    groups.sort()
    quota = min(max(0, limit - len(result)), limit // 2)
    # Prefer coherent small groups, but allow a second direction to compete.
    semantic = []
    for _, _, ids in groups:
        for i in ids[:3]:
            if i not in semantic:
                semantic.append(i)
    for i in semantic[:quota] + list(baseline) + list(ordered):
        if i not in result and len(result) < max(limit, len(required)):
            result.append(int(i))
    return result
