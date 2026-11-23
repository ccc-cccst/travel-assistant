"""Post-run rule counters and bounded log examples (not full event frequencies)."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.compare_trip_recall import read,save


def main(root):
    rows=read(root/'RESULTS.json');out=[]
    for r in rows:
        folder=root/(r['case']+'__'+r['arm'])/'planning';counts=Counter();examples={};events=[]
        for path in (folder/'cache').rglob('*.log'):
            with path.open(encoding='utf-8',errors='replace') as stream:
                for number,line in enumerate(stream,1):
                    pos=line.find('PRUNE {')
                    if pos<0:continue
                    try:item=json.loads(line[pos+6:])
                    except (ValueError,TypeError):continue
                    key=json.dumps(item,ensure_ascii=False,sort_keys=True)
                    counts[key]+=1
                    if key not in examples:examples[key]={'path':str(path.relative_to(root)),'line':number,'prune':item}
        if (folder/'combinations.json').exists():events=read(folder/'combinations.json')
        stats=r['run'].get('stats',{});wall=r['run'].get('wall_seconds')
        calls=[json.loads(s) for s in (folder/'requests.jsonl').read_text(encoding='utf-8').splitlines()] if (folder/'requests.jsonl').exists() else []
        day_calls=[c for c in calls if c['phase']=='day_sets']
        out.append({'case':r['case'],'arm':r['arm'],'state':r['state'],'wall_seconds':wall,
            'stop_reason':r['run'].get('stop_reason'),'selected_legal':r['selected_legal'],
            'day_model_calls':len(day_calls),'unique_day_model_request_hashes':len({c['request_sha256'] for c in day_calls}),
            'cpu_seconds':r['run'].get('cpu_seconds'),'model_seconds':r['model_seconds'],
            'model_fraction':r['model_seconds']/wall if wall else None,
            'dfs_inclusive_seconds':stats.get('dfs',{}).get('inclusive_seconds',0),
            'dfs_exclusive_seconds':stats.get('dfs',{}).get('exclusive_seconds',0),
            'route_compute':stats.get('route_compute',{}),'route_lookup':stats.get('route_lookup',{}),
            'prune_counts':r['run'].get('prune_counts'),
            'prune_log_scope':'record_prune records exact per-rule counters but prints at most the first three examples per rule; logged example counts are NOT full rejection frequencies',
            'combination_events':dict(Counter(e['event'] for e in events)),
            'logged_prune_examples':[{'logged_count':count,**examples[key]} for key,count in counts.most_common(8)]})
    save(root/'BOTTLENECKS.json',out)
    print(json.dumps({'runs':len(rows),'budget_reached':[{'case':r['case'],'arm':r['arm'],'wall':r['wall_seconds'],'cpu':r['cpu_seconds'],'model':r['model_seconds'],'selected_legal':r['selected_legal']} for r in out if r['stop_reason']=='matched_wall_budget']},ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]).resolve())
