"""Match typed ticket and lodging requirements against snapshot fields."""

import re


RAIL_CLASSES = {
    '高铁': ('高铁', None),
    '高铁G': ('高铁', 'G'),
    '动车': ('动车', None),
    '动车D': ('动车', 'D'),
}


def ticket_matches(kind, row, options):
    for option in options:
        if option == '飞机' and kind == 'airplane':
            return True
        if kind != 'train':
            continue
        if option == '火车':
            return True
        if option in RAIL_CLASSES:
            train_type, prefix = RAIL_CLASSES[option]
            if row.get('TrainType') == train_type and (
                prefix is None or re.fullmatch(rf'{prefix}\d+', str(row.get('TrainID', '')))
            ):
                return True
    return False


def required_features_present(value, required, options=None):
    observed = set(re.split(r'\s*[|,，;；、]\s*', value.strip())) if isinstance(value, str) else set()
    return all(observed.intersection((options or {}).get(feature, (feature,))) for feature in required)
