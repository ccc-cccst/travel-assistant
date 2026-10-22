"""Convert the downloaded CSV release to the upstream offline JSON layout."""
import ast
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT / 'ChinaTravel'
SOURCE = ROOT / 'datasets' / 'ChinaTravel'
sys.path.insert(0, str(REPO))
from chinatravel.data.load_datasets import load_query


def parse_record(row):
    result = dict(row)
    for key in ('days', 'people_number'):
        if key in result and result[key] != '':
            result[key] = int(result[key])
    for key in ('limit_rooms', 'limits_room_type', 'official_phase2_evaluation'):
        if key in result and result[key] != '':
            if result[key].lower() not in {'true', 'false'}:
                raise ValueError((key, result[key]))
            result[key] = result[key].lower() == 'true'
    for key in ('hard_logic_py', 'hard_logic_nl', 'constraint_keys'):
        if key in result and result[key] != '':
            try:
                value = json.loads(result[key])
            except json.JSONDecodeError:
                value = ast.literal_eval(result[key])
            if not isinstance(value, list):
                raise ValueError((key, type(value)))
            result[key] = value
    if not result.get('hard_logic_py'):
        result.pop('hard_logic_py', None)
    return result


def convert(name, sources, lang):
    base = REPO / 'chinatravel' / 'data'
    if lang == 'en':
        base /= 'en'
    destination = base / name
    destination.mkdir(parents=True, exist_ok=True)
    uids = []
    for source in sources:
        with source.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                record = parse_record(row)
                uid = record['uid']
                if not uid or Path(uid).name != uid or '/' in uid or '\\' in uid:
                    raise ValueError(uid)
                target = destination / (uid + '.json')
                content = json.dumps(record, ensure_ascii=False, indent=2) + '\n'
                if target.exists():
                    if target.read_text(encoding='utf-8') != content:
                        raise FileExistsError(target)
                else:
                    target.write_text(content, encoding='utf-8')
                uids.append(uid)
    if len(uids) != len(set(uids)):
        raise ValueError('Duplicate UIDs in ' + name)
    split_file = REPO / 'chinatravel' / 'evaluation' / 'default_splits' / (name + '.txt')
    content = '\n'.join(uids) + '\n'
    if split_file.exists() and split_file.read_text(encoding='utf-8') != content:
        raise FileExistsError(split_file)
    split_file.write_text(content, encoding='utf-8')
    args = SimpleNamespace(splits=name, lang=lang, oracle_translation=True)
    loaded_ids, records = load_query(args)
    assert loaded_ids == uids and len(records) == len(uids)
    args.oracle_translation = False
    _, public_records = load_query(args)
    assert all('hard_logic_py' not in record for record in public_records.values())
    print(name, lang, len(uids), 'offline load verified')
    return {'split': name, 'lang': lang, 'records': len(uids), 'example_uid': uids[0]}


def prepare_experiment_subsets():
    reports = []
    base = REPO / 'chinatravel' / 'data'
    for selection in sorted((ROOT / 'reproduction' / 'splits').glob('*.txt')):
        ids = selection.read_text(encoding='utf-8').splitlines()
        target = base / selection.stem
        target.mkdir(parents=True, exist_ok=True)
        for uid in ids:
            candidates = [base / split / (uid + '.json') for split in ('easy_local', 'medium_local', 'human_local')]
            source = next((p for p in candidates if p.exists()), None)
            if source is None:
                raise FileNotFoundError('Missing selected query: ' + uid)
            output = target / (uid + '.json')
            content = source.read_bytes()
            if output.exists() and output.read_bytes() != content:
                raise FileExistsError(output)
            output.write_bytes(content)
        split_file = REPO / 'chinatravel' / 'evaluation' / 'default_splits' / selection.name
        content = selection.read_bytes()
        if split_file.exists() and split_file.read_bytes() != content:
            raise FileExistsError(split_file)
        split_file.write_bytes(content)
        reports.append({'split': selection.stem, 'records': len(ids), 'lang': 'zh'})
    return reports


def main():
    report = []
    for source in sorted(SOURCE.glob('*.csv')):
        report.append(convert(source.stem + '_local', [source], 'zh'))
    phase2 = sorted((SOURCE / 'phase2').glob('part-*.csv'))
    if len(phase2) != 2:
        raise ValueError('Expected two Phase 2 files')
    report.append(convert('phase2_full_local', phase2, 'en'))
    report.append(convert('phase2_test_local', [SOURCE / 'phase2' / 'competition_test.csv'], 'en'))
    report.extend(prepare_experiment_subsets())
    (ROOT / 'local_query_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
