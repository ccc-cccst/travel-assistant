"""Full natural-language 2x2 model/planner comparison on three easy requests."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(Path(__file__).resolve().parent)]
from run_activity_granularity import audit, read, save


def extra_metrics(out, result):
    if "wall_seconds" not in result:
        return {}
    pool=read(out/"pool.json")
    selected=next((item for item in pool if item["fingerprint"]==result["selected_fingerprint"]),None)
    signatures=[]
    for item in pool:
        signatures.append(tuple(tuple(sorted(a["position"] for a in day["activities"] if a["type"]=="attraction"))
                                for day in item["plan"]["itinerary"]))
    actual_minutes=[]
    if selected:
        for day in selected["plan"]["itinerary"]:
            for act in day["activities"]:
                if act["type"]=="attraction":
                    start,end=act["start_time"].split(":"),act["end_time"].split(":")
                    actual_minutes.append(60*(int(end[0])-int(start[0]))+int(end[1])-int(start[1]))
    cache=[read(p) for p in out.glob("cache/translation_*/*.json")]
    translated=cache[0] if len(cache)==1 else {}
    ledger=[json.loads(line) for line in (out/"requests.jsonl").read_text(encoding="utf-8").splitlines()]
    run=read(out/"run.json")
    return {
        "hard_logic":translated.get("hard_logic"),"hard_logic_py":translated.get("hard_logic_py"),
        "translation_mode":translated.get("translation_mode"),
        "translation_needs_review":bool(run.get("translation_fallback")),
        "output_tokens":sum(row.get("output_tokens") or 0 for row in ledger),
        "distinct_multiday_sets":len(set(signatures)),
        "actual_sightseeing_minutes":sum(actual_minutes),
        "shortest_attraction_visit":min(actual_minutes,default=None),
        "short_visits_below_30":sum(m<30 for m in actual_minutes),
        "cpu_seconds":run["cpu_seconds"],"route_compute":run["stats"].get("route_compute"),
        "prune_counts":run["prune_counts"],
        "time_adjustments":selected["score"].get("time_adjustments") if selected else None,
    }


def campaign(args):
    from chinatravel.environment.sandbox.__main__ import configuration
    settings=configuration(args.data)
    os.environ.update(settings)
    args.out.mkdir(parents=True,exist_ok=False)
    source_files=[p for p in (ROOT/"chinatravel").rglob("*.py")
                  if "database" not in p.parts and "__pycache__" not in p.parts]
    for folder in ("experiments/c_only","experiments/timing_probe","experiments/evaluation"):
        source_files.extend((ROOT/folder).glob("*.py"))
    queries=[ROOT/"experiments/c_only/queries"/(case+".json") for case in args.cases]
    source_files.extend(queries)
    for filename in ("test_chinatravel_dsl.py","test_flexible_time.py","test_day_sets.py"):
        source_files.append(ROOT/"tests"/filename)
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    for p in source_files:
        target=args.out/"source_snapshot"/p.relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
    orders=(
        (("qwen","baseline"),("api","day_sets"),("api","baseline"),("qwen","day_sets")),
        (("api","day_sets"),("qwen","baseline"),("qwen","day_sets"),("api","baseline")),
        (("qwen","day_sets"),("api","baseline"),("api","day_sets"),("qwen","baseline")),
    )
    jobs=[(case,provider,arm) for index,case in enumerate(args.cases)
          for provider,arm in orders[index%len(orders)]]
    save(args.out/"MANIFEST.json",{
        "cases":args.cases,"jobs":jobs,"models":{"qwen":"qwen3.5-4b-q8","api":"deepseek-flash"},
        "api_thinking":"disabled","qwen_seed":20261112,"api_seed_sent":False,
        "temperature":0,"max_tokens":4096,"budget_seconds":args.seconds,"candidate_target":20,
        "time_policy":"flexible","source_and_request_sha256":hashes,
        "snapshot":read(args.data/"install.json")["source"],"data":str(args.data),
        "local_endpoint":args.endpoint,"repeats_per_cell":1,
        "translation":"fresh for each job, no oracle in planning",
    })
    results={}
    for case,provider,arm in jobs:
        label=f"{case}__{provider}__{arm}"
        out=args.out/label
        save(args.out/"STATUS.json",{"finished":False,"job":label,"started":time.time(),
                                     "completed":len(results),"total":len(jobs)})
        print("START "+label,flush=True)
        base=[sys.executable,"-B","-X","utf8"]
        if provider=="api":
            cmd=base+[str(Path(__file__).with_name("api_speed.py")),"full","--defer-audit"]
        else:
            cmd=base+[str(Path(__file__).with_name("run_activity_granularity.py")),"worker",
                      "--endpoint",args.endpoint]
        cmd+=["--query",str(ROOT/"experiments/c_only/queries"/(case+".json")),"--arm",arm,
              "--time-policy","flexible","--seconds",str(args.seconds),"--out",str(out)]
        env={**os.environ,**settings,"CHINATRAVEL_TRANSLATION_MODE":"chinatravel","PYTHONUNBUFFERED":"1"}
        if provider=="qwen":
            env.pop("DEEPSEEK_API_KEY",None)
        started=time.perf_counter()
        observed={}
        try:
            with (args.out/(label+".log")).open("x",encoding="utf-8") as log:
                worker=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                                      timeout=args.seconds+70)
            worker_wall=time.perf_counter()-started
            if worker.returncode==0 and (out/"pool.json").exists():
                audit_started=time.perf_counter()
                observed=audit(out,read(ROOT/"experiments/c_only/queries"/(case+".json")),"flexible")
                observed["independent_audit_seconds"]=time.perf_counter()-audit_started
                observed["worker_process_seconds"]=worker_wall
                observed["verified_after_process_start_seconds"]=worker_wall+observed["independent_audit_seconds"]
                observed["extra"]=extra_metrics(out,observed)
                save(out/"comparison.json",observed)
            else:
                observed={"process_error":worker.returncode,"worker_process_seconds":worker_wall}
        except subprocess.TimeoutExpired:
            observed={"process_error":"outer_watchdog","worker_process_seconds":time.perf_counter()-started}
        results[label]=observed
        save(args.out/"RESULTS.json",results)
        print(f"DONE {label} first={observed.get('first_independent_legal_seconds')} "
              f"wall={observed.get('wall_seconds')} legal={observed.get('selected_legal')}",flush=True)
    changed=[p for p,h in hashes.items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    summary={"source_changes_during_collection":changed,"groups":{},"translation_comparisons":{}}
    for provider,arm in (("qwen","baseline"),("qwen","day_sets"),("api","baseline"),("api","day_sets")):
        rows=[results[f"{case}__{provider}__{arm}"] for case in args.cases]
        good=[r for r in rows if "wall_seconds" in r]
        summary["groups"][provider+"__"+arm]={
            "complete":len(good),"legal":sum(r["selected_legal"] for r in good),
            "timeouts":sum(r["stop_reason"]=="matched_wall_budget" for r in good),
            "wall_seconds":sum(r["wall_seconds"] for r in good),
            "model_seconds":sum(r["model_seconds"] for r in good),
            "model_calls":sum(r["model_calls"] for r in good),
            "output_tokens":sum(r["extra"]["output_tokens"] for r in good),
        }
    for case in args.cases:
        values=[results[f"{case}__{p}__{a}"].get("extra",{}).get("hard_logic")
                for p,a in orders[0]]
        summary["translation_comparisons"][case]={
            "all_available":all(value is not None for value in values),
            "all_equal":all(value is not None and value==values[0] for value in values),
            "all_constraint_text_sets_equal":all(value is not None and set(value)==set(values[0] or []) for value in values),
        }
    save(args.out/"SUMMARY.json",summary)
    save(args.out/"STATUS.json",{"finished":True,"completed":len(results),"total":len(jobs)})
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    if changed:
        raise RuntimeError("Source changed during collection")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--data",type=Path,default=ROOT.parent/"ShortTrip/artifacts/data/5e0ee8a")
    parser.add_argument("--cases",nargs="+",default=["easy_1","easy_2","easy_3"],
                        choices=["easy_1","easy_2","easy_3","easy_4","easy_5"])
    parser.add_argument("--seconds",type=float,default=240)
    parser.add_argument("--endpoint",default="http://127.0.0.1:18088/v1")
    args=parser.parse_args()
    args.out=args.out.resolve()
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is required for the authorized API trials")
    campaign(args)


if __name__=="__main__":
    main()

