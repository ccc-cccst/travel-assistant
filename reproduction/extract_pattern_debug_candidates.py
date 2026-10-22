"""Preserve logged candidates from externally interrupted runs, never as results."""
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'ChinaTravel'
OUT = ROOT / 'reproduction/flash_patterns_20261017'
sys.path.insert(0, str(REPO))
from chinatravel.symbol_verification.hard_constraint import evaluate_constraints_py


def main():
    status = json.loads((OUT/'status.json').read_text(encoding='utf-8'))
    destination = OUT/'debug_candidates'
    destination.mkdir(exist_ok=True)
    for case in status['cases']:
        if case['status'] != 'wall_timeout':
            continue
        log = OUT/(case['uid']+'.log')
        with log.open('rb') as stream:
            stream.seek(0,2)
            stream.seek(max(0,stream.tell()-8*1024*1024))
            lines = stream.read().decode('utf-8',errors='replace').splitlines()
        candidate = None
        for line in reversed(lines):
            if line.startswith("{'people_number':"):
                try:
                    value = ast.literal_eval(line)
                    if isinstance(value,dict) and value.get('itinerary'):
                        candidate = value
                        break
                except (SyntaxError, ValueError):
                    continue
        if candidate is None:
            print('No complete logged candidate:',case['uid'])
            continue
        uid = case['uid']
        reference = json.loads((REPO/'chinatravel/data/patterns14_local'/(uid+'.json')).read_text(encoding='utf-8'))
        generated = json.loads((REPO/'cache/translation_repro-75be0df8e14c_reflect'/(uid+'.json')).read_text(encoding='utf-8'))
        cost = sum(float(a.get('cost',0))+sum(float(t.get('cost',0)) for t in a.get('transports',[]))
                   for day in candidate['itinerary'] for a in day['activities'])
        record = {'kind':'Last complete validation candidate found in log; NOT the final model/agent result and NOT included in aggregate scores.',
                  'uid':uid,'source_log':str(log),'candidate_sha256':hashlib.sha256(json.dumps(candidate,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                  'sandbox_cost_cny':round(cost,2),'reference_logic_check':[bool(v) for v in evaluate_constraints_py(reference['hard_logic_py'],candidate)],
                  'generated_logic_check':[bool(v) for v in evaluate_constraints_py(generated['hard_logic_py'],candidate)],'candidate':candidate}
        (destination/(uid+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(uid,'debug only; cost',round(cost,2),'reference checks',record['reference_logic_check'])


if __name__ == '__main__':
    main()
