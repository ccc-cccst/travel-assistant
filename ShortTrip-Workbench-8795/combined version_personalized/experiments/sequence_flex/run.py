"""Replan real frozen candidate sets; this is NOT a new end-to-end LLM benchmark."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import shutil
from collections import Counter
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def read(p):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save(p,value):
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=ROOT/"result/experiments/EXP034_candidates10")
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--cases",nargs="+")
    parser.add_argument("--versions",nargs="+",default=["baseline","day_sets"])
    parser.add_argument("--case-seconds",type=float,default=120)
    parser.add_argument("--iteration",choices=["order_only","quality_select"],default="quality_select")
    parser.add_argument("--cost-weight",type=float,default=.08)
    parser.add_argument("--max-cost-ratio",type=float,default=None)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    from chinatravel.environment.sandbox.__main__ import configuration
    os.environ.update(configuration(ROOT.parent/"ShortTrip/artifacts/data/5e0ee8a"))
    from experiments.sequence_flex.adapter import Catalog,reschedule
    from experiments.sequence_flex.core import Policy,review,public_view,metrics
    from experiments.evaluation.validation import validate_plan
    from experiments.evaluation.features import score_plan
    policy=Policy(route_cost_weight=args.cost_weight)
    manifest=read(args.source/"MANIFEST.json")
    cases=args.cases or manifest["cases"]
    sources=[*Path(__file__).parent.glob("*.py"),ROOT/"tests/test_sequence_flex.py"]
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    for p in sources:
        dest=args.out/"source"/p.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,dest)
    save(args.out/"MANIFEST.json",{"date":"2026-11-19","method":"sequence_flex_v4",
        "study":"frozen upstream sets, real local rerouting and independent validation",
        "cases":cases,"versions":args.versions,"iteration":args.iteration,"policy":asdict(policy),
        "source_sha256":hashes,"original_manifest_sha256":hashlib.sha256((args.source/"MANIFEST.json").read_bytes()).hexdigest(),
        "case_seconds":args.case_seconds,"max_cost_ratio":args.max_cost_ratio,"model_requests":0,"P_and_E_evaluated":False,
        "claim_limit":"Local repair diagnostic; input failure coverage and upstream LLM/route recall are unchanged."})
    catalog=Catalog()
    rows=[]
    for case in cases:
        query=read(args.source/"queries"/(case+".json"))
        for version in args.versions:
            label=case+"__"+version
            origin=args.source/"runs"/label
            pool=read(origin/"pool.json")
            begin=time.perf_counter()
            result={"case":case,"source_version":version,"input_candidates":len(pool),
                    "source_pool_sha256":hashlib.sha256((origin/"pool.json").read_bytes()).hexdigest()}
            print("START "+label,flush=True)
            out=args.out/label
            out.mkdir()
            if not pool:
                result.update(status="upstream_no_candidates",local_seconds=0,legal_candidates=0,ready_candidates=0)
                rows.append(result)
                save(out/"result.json",result)
                save(args.out/"RESULTS.json",rows)
                continue
            actual=read(next(origin.glob("cache/translation*/*.json")))
            selected=min(pool,key=lambda x:(x["score"]["total_penalty"],x["ordinal"]))
            result["before"]=review(selected["plan"],policy,query.get("allow_night_travel",False))
            failures=Counter()
            generated=[]
            seen=set()
            for candidate in pool:
                remaining=args.case_seconds-(time.perf_counter()-begin)
                if remaining<=0:
                    failures["case_budget"]+=1
                    break
                new,diagnostic=reschedule(candidate["plan"],actual,catalog,policy,min(20,remaining))
                if new is None:
                    failures[diagnostic["reason"]]+=1
                    continue
                identity=json.dumps(new["itinerary"],sort_keys=True,ensure_ascii=False)
                if identity in seen:
                    continue
                seen.add(identity)
                check=validate_plan(new,actual,config={"time_policy":"flexible"})
                legal=all(c["status"] in ("pass","not_applicable") for c in check["checks"])
                concerns=review(new,policy,query.get("allow_night_travel",False))
                quick=score_plan(new,actual,profile="fast_rules_v0",requirements={"reviewed":False,"items":[]})
                gold=validate_plan(new,query,config={"time_policy":"flexible"})
                generated.append({"source_ordinal":candidate["ordinal"],"plan":new,"diagnostic":diagnostic,
                    "actual_dsl_legal":legal,"validation":check,"gold_validation":gold,
                    "review":concerns,"Q_rule":quick["quality_score"],"Q_components":quick["components"]})
            legal=[c for c in generated if c["actual_dsl_legal"]]
            selectable=[c for c in legal if args.max_cost_ratio is None or
                        c["review"]["metrics"]["cost"]<=result["before"]["metrics"]["cost"]*args.max_cost_ratio]
            def rank(c):
                m=c["review"]["metrics"]
                if args.iteration=="order_only":
                    return (m["city_transit_minutes"],m["walk_minutes"],m["cost"])
                return (len(c["review"]["issues"]),-(c["Q_rule"] or 0),m["city_transit_minutes"],m["cost"])
            winner=min(selectable,key=rank) if selectable else None
            result.update(local_seconds=time.perf_counter()-begin,generated=len(generated),
                          legal_candidates=len(legal),ready_candidates=sum(c["review"]["delivery_ready"] for c in legal),
                          cost_guard_rejected=len(legal)-len(selectable),
                          failures=dict(failures),status="draft_ready" if winner and winner["review"]["delivery_ready"]
                          else "needs_replanning_or_confirmation" if winner else "no_feasible_repair",
                          selected_source_ordinal=winner["source_ordinal"] if winner else None)
            if winner:
                result["after"]=winner["review"]
                result["Q_rule"]=winner["Q_rule"]
                result["gold_legal"]=all(c["status"] in ("pass","not_applicable") for c in winner["gold_validation"]["checks"])
                save(out/"witness.json",winner["plan"])
                save(out/"sequence.json",public_view(winner["plan"],actual.get("appointments",[])))
            save(out/"candidates.json",generated)
            save(out/"result.json",result)
            rows.append(result)
            save(args.out/"RESULTS.json",rows)
            print("DONE "+label+" "+result["status"]+" legal="+str(result["legal_candidates"])+
                  " seconds="+str(round(result["local_seconds"],2)),flush=True)
    summary={"rows":len(rows),"local_seconds":sum(r["local_seconds"] for r in rows),
             "statuses":dict(Counter(r["status"] for r in rows)),
             "route_queries":catalog.route_queries,"uncached_routes":catalog.route_misses,
             "source_unchanged":all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items()),
             "old_source_unchanged":all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h
                                      for p,h in manifest["source_sha256"].items())}
    save(args.out/"SUMMARY.json",summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()

