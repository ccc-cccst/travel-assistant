"""Trace existing preference variants to parsed objectives without planner calls."""
import csv
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.compare_trip_recall import read,save


def main(out):
    audit=read(out/'CORPUS_PARSE_AUDIT.json')
    originals={a['uid']:a for a in audit if a['source'] in ('easy','medium','human')}
    expect=[('focus','more_attractions'),('mobility','less_transit'),('focus','near_restaurants'),
            ('focus','food_share'),('focus','lodging_cost'),('anchor',None)]
    summaries=[];details=[]
    for i,wanted in enumerate(expect):
        ns=f'preference{i}_base50';source=list(csv.DictReader((ROOT.parent/'datasets/ChinaTravel'/(ns+'.csv')).open(encoding='utf-8-sig')))
        text={r['uid']:r['nature_language'] for r in source}
        group=[]
        for row in (a for a in audit if a['source']==ns):
            prefs={(p['dimension'],p['target']) for p in row.get('preferences',[])}
            base=originals[row['uid'][2:]];baseprefs={(p['dimension'],p['target']) for p in base.get('preferences',[])}
            hit=any(d==wanted[0] and (wanted[1] is None or t==wanted[1]) for d,t in prefs)
            unexpected=[p for p in row.get('preferences',[]) if (p['dimension'],p['target']) not in baseprefs and
                        (p['dimension'],p['target'])==('focus','more_attractions') and i!=0]
            group.append({'uid':row['uid'],'expected_target_found':hit,'additional_more_attractions':unexpected,
                          'prompt':text[row['uid']],'parsed':row.get('preferences',[])})
        summaries.append({'source':ns,'n':len(group),'expected_target_found':sum(r['expected_target_found'] for r in group),
                         'additional_more_attractions':sum(bool(r['additional_more_attractions']) for r in group)})
        details.extend({'source':ns,**r} for r in group)
    save(out/'PREFERENCE_MAPPING_AUDIT.json',{'summary':summaries,'details':details,
        'interpretation':'new objectives are compared with the same original base; extra objective requires prompt review before calling it incorrect'})
    print(json.dumps(summaries,ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]))
