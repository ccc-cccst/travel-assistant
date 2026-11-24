"""Read local design inputs and write an isolated audit; never run a planner/model."""
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BASE = WORKSPACE / "combined version_1"
DATA = WORKSPACE / "ShortTrip/artifacts/data/5e0ee8a"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_previews():
    dataset = WORKSPACE / "datasets/ChinaTravel"
    easy_path = BASE / "experiments/c_only/queries/easy_1.json"
    easy = json.loads(easy_path.read_text(encoding="utf-8-sig"))
    medium_path = dataset / "medium.csv"
    medium = next(r for r in read_csv(medium_path) if r["uid"] == "e20241028161417958159")
    human_path = dataset / "human.csv"
    human = next(r for r in read_csv(human_path) if r["uid"] == "h20241029143506461809")
    previews = []
    for record, path in [(easy, easy_path), (medium, medium_path), (human, human_path)]:
        text = record["nature_language"]
        item = {
            "schema_version": "trip_request_v1_draft",
            "request_id": record["uid"], "request_revision": 1,
            "source": {"path": str(path), "file_sha256": digest(path),
                       "uid": record["uid"], "base_uid": record["uid"],
                       "difficulty": record["tag"], "method": "assistant_design_preview"},
            "raw_text": text,
            "trip": {"origin_city": record["start_city"], "destination_city": record["target_city"],
                     "days": int(record["days"]), "people": int(record["people_number"]),
                     "start_date": None, "return_city": None},
            "hard_constraints": [], "preferences": [], "locked_events": [],
            "unmapped_requirements": [], "clarifications": [],
            "evidence": [{"id": "public_trip_fields", "kind": "source_fields",
                          "paths": ["start_city", "target_city", "days", "people_number"]}],
            "execution_assumptions": [], "scope_status": "single_destination_positive_days",
            "review_status": "assistant_reviewed_not_user_confirmed",
        }

        def evidence(quote):
            start = text.index(quote)
            eid = "text_" + str(len(item["evidence"]))
            item["evidence"].append({"id": eid, "kind": "text_span", "quote": quote,
                                     "start": start, "end": start + len(quote)})
            return [eid]

        def hard(identifier, field, op, value, unit, scope, quote, confirmation="extracted"):
            item["hard_constraints"].append({"id": identifier, "field": field, "op": op,
                "value": value, "unit": unit, "scope": scope, "evidence_ids": evidence(quote),
                "confirmation": confirmation})

        def preference(identifier, dimension, target, quote, verification="pending_catalog_mapping"):
            item["preferences"].append({"id": identifier, "dimension": dimension, "target": target,
                "direction": "prefer", "priority": "normal", "scope": "trip",
                "evidence_ids": evidence(quote), "verification": verification})

        if record is easy:
            hard("train", "intercity_mode", "allowed_only", ["train"], None,
                 "trip_intercity_mode", "选择火车出行")
        elif record is medium:
            hard("budget", "cost", "lte", 6000, "CNY", "unspecified",
                 "预算6000人民币", "needs_budget_scope_confirmation")
            hard("bed", "beds_per_room", "eq", 1, "bed", "each_room", "住大床房")
            hard("outbound_train", "intercity_mode", "allowed_only", ["train"], None,
                 "outbound", "坐火车去")
            preference("food", "cuisine", "江浙菜", "想吃江浙菜")
            preference("lodging", "lodging_category", "民宿", "住的酒店最好是民宿")
            item["clarifications"].append({"id": "budget_scope", "reason": "原文未明确整团或每人预算",
                "affects": ["budget"], "blocking_for": ["verified_budget_claim"]})
            item["unmapped_requirements"].append({"id": "room_bed_size", "text": "住大床房",
                "reason": "现有字段能核验每间一床，不能证明床尺寸或实时售卖大床房型",
                "linked_requirement": "bed", "status": "requires_external_confirmation"})
        else:
            item["trip"]["companion_context"] = "与女朋友同行"
            item["evidence"].append({"id": "companion_context", "kind": "text_span",
                "quote": "我和女朋友", "start": text.index("我和女朋友"),
                "end": text.index("我和女朋友") + len("我和女朋友")})
            preference("nature", "interest", "nature", "看看自然风光")
            preference("relaxed", "pace", "relaxed", "计划不要太累", "requires_plan_metrics")
            preference("relaxation", "semantic_experience", "放松心情", "放松心情",
                       "semantic_preference_not_independently_verified")
            item["unmapped_requirements"].append({"id": "wellness", "text": "养生为主",
                "evidence_ids": evidence("养生为主"), "status": "needs_detail",
                "reason": "未明确具体活动，不推断温泉、SPA、早睡或步行上限"})
            item["clarifications"].append({"id": "wellness_detail", "reason": "可补充具体养生体验",
                "affects": ["wellness"], "blocking_for": []})
        assert all(text[e["start"]:e["end"]] == e["quote"] for e in item["evidence"] if e["kind"] == "text_span")
        previews.append(item)
    return previews


def inventory():
    sys.path.insert(0, str(BASE))
    from chinatravel.environment.sandbox.__main__ import configuration
    os.environ.update(configuration(DATA))
    from chinatravel.environment.world_env import WorldEnv
    env = WorldEnv(lang="zh")
    result = {"date": "2026-11-19", "baseline": str(BASE), "data_root": str(DATA),
              "model_called": False, "planning_run": False, "tables": {}, "queries": {},
              "warning": "Nonempty fields and unique name matches do not certify real-world accuracy."}
    modules = [("attractions", "attraction", "type"), ("restaurants", "restaurant", "cuisine"),
               ("accommodations", "accommodation", "featurehoteltype")]
    for module, _, field in modules:
        cities = {}
        for city, frame in getattr(env, module).data.items():
            values = frame[field].fillna("").astype(str).str.strip()
            cities[city] = {"total": len(frame), "nonempty": int((values.ne("") & values.str.upper().ne("UNKNOWN")).sum())}
        result["tables"][module] = {"field": field, "total": sum(x["total"] for x in cities.values()),
                                   "nonempty": sum(x["nonempty"] for x in cities.values()), "cities": cities}
    catalog = DATA / "catalog_v1_1_20261022/catalog.sqlite3"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        result["catalog"] = {"path": str(catalog), "sha256": digest(catalog),
            "counts": {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                       for t in ("records", "tags", "claims")},
            "tags": dict(db.execute("SELECT tag,COUNT(*) FROM tags GROUP BY tag")),
            "cities": [x[0] for x in db.execute("SELECT DISTINCT city FROM records ORDER BY city")]}
        index = defaultdict(list)
        for rid, kind, city, name, payload in db.execute("SELECT id,kind,city,name,payload FROM records WHERE lang='zh'"):
            index[(kind, city, name)].append((rid, json.loads(payload)))
        result["name_match_candidates"] = {}
        for module, kind, _ in modules:
            counts = Counter()
            for city, frame in getattr(env, module).data.items():
                for row in frame.to_dict("records"):
                    matches = index[(kind, city, row["name"])]
                    counts["total"] += 1
                    counts["missing" if not matches else "unique" if len(matches) == 1 else "ambiguous"] += 1
                    if len(matches) == 1:
                        old_id = matches[0][1]["data"].get("id")
                        try:
                            same = float(old_id) == float(row["id"])
                        except (TypeError, ValueError):
                            same = str(old_id) == str(row["id"])
                        counts["id_equal" if same else "id_changed"] += 1
            result["name_match_candidates"][module] = dict(counts)
    for path in sorted((WORKSPACE / "datasets/ChinaTravel").glob("*.csv")):
        rows = read_csv(path)
        result["queries"][path.name] = {"path": str(path), "sha256": digest(path), "rows": len(rows),
            "days": dict(Counter(r.get("days", "unspecified") for r in rows))}
    manifest = DATA / "query_sets/xian11/manifest.json"
    result["xian_query_counts"] = json.loads(manifest.read_text(encoding="utf-8-sig"))["counts"]
    upstream = WORKSPACE / "tmp/nov16-sources/five-city-5e0ee8a/data/xian11_merged_v17_tags/tags_v17.jsonl.gz"
    with gzip.open(upstream, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    statuses = Counter()
    for row in rows:
        payload = row["payload"]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        statuses[payload.get("status")] += 1
    result["uninstalled_v17"] = {"path": str(upstream), "sha256": digest(upstream), "rows": len(rows),
        "cities": dict(Counter(r["city"] for r in rows)), "status_counts": dict(statuses)}
    paths = ["experiments/timing_probe/run_planner.py", "experiments/timing_probe/day_sets.py",
             "experiments/timing_probe/combo_schedule.py", "experiments/timing_probe/run_activity_granularity.py",
             "chinatravel/agent/nesy_agent/chinatravel_dsl.py", "chinatravel/data/load_datasets.py",
             "chinatravel/soft_constraints/scoring.py", "chinatravel/soft_constraints/pool.py"]
    result["reviewed_source_sha256"] = {p: digest(BASE / p) for p in paths}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    if not output.is_relative_to((ROOT / "result").resolve()):
        parser.error("Output must be inside this design version's result directory")
    if output.exists():
        parser.error("Output already exists; choose a fresh directory")
    audit = inventory()
    previews = build_previews()
    output.mkdir(parents=True, exist_ok=False)
    (output / "input_inventory.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "request_preview.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in previews), encoding="utf-8")
    print(json.dumps({"output": str(output), "preview_count": len(previews), "model_calls": 0,
                      "main_tables": {k: (v["total"], v["nonempty"]) for k, v in audit["tables"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
