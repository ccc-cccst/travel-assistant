"""Seal unused ChinaTravel request families, then run the frozen HTTP acceptance set."""
import argparse
import csv
from getpass import getpass
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from audit_new_trip_delivery import CORPUS, LEVELS, ROOT, fresh, read, run_http, save, text_family

EXP = ROOT/'result/experiments'
OUT = EXP/'P036_delivery_adoption'


def freeze(out=OUT, *, seed='P036/v1/'):
    manifest = out/'FROZEN30.json'
    if manifest.exists():
        return read(manifest)
    excluded_ids, excluded_uid, excluded_text = set(), set(), set()
    pattern = re.compile(r'(?<![a-zA-Z0-9])(easy|human|medium)_0*(\d+)(?!\d)')
    for folder in EXP.iterdir():
        if not folder.is_dir() or folder == out:
            continue
        for parent, dirs, files in os.walk(folder):
            for name in dirs:
                excluded_ids.update((k, int(i)) for k, i in pattern.findall(name))
            for name in files:
                if not any(k in name.upper() for k in ('MANIFEST','FROZEN','GOLD_DRAFT','SUMMARY','REVIEW')):
                    continue
                path = Path(parent)/name
                if path.suffix.lower() not in ('.json','.md','.jsonl'):
                    continue
                content = path.read_text(encoding='utf-8-sig')
                excluded_ids.update((k, int(i)) for k, i in pattern.findall(content))
    gold = EXP/'P007_tripspec_evaluation/GOLD_DRAFT.jsonl'
    for line in gold.read_text(encoding='utf-8-sig').splitlines():
        row = json.loads(line)
        excluded_uid.update([row.get('uid'), row.get('family_uid')])
        excluded_text.add(text_family(row['raw_text']))
    def rows(name):
        with (CORPUS/(name+'.csv')).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))
    for row in rows('preference_base50'):
        excluded_uid.add(row['uid'])
        excluded_text.add(text_family(row['nature_language']))
    corpora = {level: rows(level) for level in LEVELS}
    for level, records in corpora.items():
        for i, row in enumerate(records, 1):
            if (level,i) in excluded_ids:
                excluded_text.add(text_family(row['nature_language']))
                excluded_uid.add(row['uid'])
    cases = []
    for level, records in corpora.items():
        chosen = []
        for i, row in sorted(enumerate(records, 1), key=lambda p: sha256((seed+p[1]['uid']).encode()).digest()):
            raw, uid = row['nature_language'], row['uid']
            family = text_family(raw)
            if not family or family in excluded_text or uid in excluded_uid:
                continue
            excluded_text.add(family)
            excluded_uid.add(uid)
            chosen.append(dict(case=f'{level}_{i}', level=level, uid=uid, raw_text=raw,
                               raw_sha256=sha256(raw.encode()).hexdigest(),
                               corpus_core=dict(origin_city=row['start_city'], destination_city=row['target_city'],
                                                people=int(row['people_number']), days=int(row['days']))))
            if len(chosen)==10:
                break
        if len(chosen)!=10:
            raise ValueError(f'{level}: only {len(chosen)} unused families')
        cases.extend(chosen)
    return save(manifest,dict(cases=cases, excluded_case_ids=len(excluded_ids),
                              source_sha256={k:sha256((CORPUS/(k+'.csv')).read_bytes()).hexdigest() for k in LEVELS}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path)
    parser.add_argument('--manifest-dir',type=Path,default=OUT)
    parser.add_argument('--seed',default='P036/v1/')
    parser.add_argument('--ask-key',action='store_true')
    args=parser.parse_args()
    manifest_dir=args.manifest_dir.resolve()
    cases=freeze(manifest_dir,seed=args.seed)['cases']
    if args.run:
        key=getpass('DeepSeek API key: ') if args.ask_key else os.environ.get('DEEPSEEK_API_KEY')
        if not key:
            raise SystemExit('Real-model acceptance requires a configured key.')
        run_http(cases,fresh(args.run),api_key=key,workers=3,followup=True)
    else:
        print('Sealed',len(cases),'requests;',sha256((manifest_dir/'FROZEN30.json').read_bytes()).hexdigest())
