"""Offline human preference calibration: blinded pairs, never synthetic labels.

The review Markdown and labels template are public to annotators. Keep
private_key.json private: it contains method identities and automatic scores.
No model, planner or network client is used by this module.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


VERSION = "shorttrip-blind-calibration-v0"
WINNERS = {None, "a", "b", "tie", "uncertain"}
SCORE_FIELDS = ("quality_score", "preference_score", "composite_score")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    Path(path).write_text("".join(_json(row) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path):
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            try:
                value = json.loads(line)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Each row must be an object at {path}:{line_number}")
            rows.append(value)
    return rows


def _score(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number or null")
    if not 0 <= value <= 100:
        raise ValueError(f"{field} must be between 0 and 100")
    return float(value)


def _text(value):
    """Escape user-supplied text so it cannot create Markdown structure."""
    value = "未知" if value is None else str(value)
    for old, new in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                     ("|", "&#124;"), ("`", "&#96;"), ("[", "&#91;"), ("]", "&#93;")):
        value = value.replace(old, new)
    return value.replace("\r", " ").replace("\n", " ")


def _query_text(query):
    for field in ("nature_language", "text", "user_request", "query", "nature_language_en"):
        if isinstance(query.get(field), str) and query[field].strip():
            return query[field]
    return "；".join(f"{label}：{query[field]}" for field, label in (
        ("start_city", "出发地"), ("target_city", "目的地"),
        ("days", "天数"), ("people_number", "人数")) if field in query)


def _render_plan(plan):
    lines = [f"出发地：{_text(plan.get('start_city'))}；目的地：{_text(plan.get('target_city'))}；人数：{_text(plan.get('people_number'))}。", "",
             "| 天 | 时间 | 活动或交通 | 具体信息 |",
             "| --- | --- | --- | --- |"]
    for day in plan.get("itinerary", []):
        d = _text(day.get("day"))
        if day.get("date"):
            d += f"（{_text(day['date'])}）"
        for activity in day.get("activities", []):
            # Access legs precede their destination activity in the actual plan.
            for transport in activity.get("transports", []):
                detail = f"{_text(transport.get('start'))} → {_text(transport.get('end'))}"
                for key, label in (("distance", "距离km"), ("price", "单价"), ("cost", "费用"), ("tickets", "票数"),
                                   ("end_day_offset", "结束日偏移"), ("duration_minutes", "分钟")):
                    if key in transport:
                        detail += f"；{label}：{_text(transport[key])}"
                lines.append(f"| {d} | {_text(transport.get('start_time'))}–{_text(transport.get('end_time'))} | 接驳：{_text(transport.get('mode'))} | {detail} |")
            name = activity.get("position")
            if activity.get("type") in {"train", "airplane"}:
                name = f"{activity.get('start', '未知')} → {activity.get('end', '未知')}"
            details = [_text(name)]
            for key, label in (("TrainID", "车次"), ("FlightID", "航班"), ("price", "单价"),
                               ("cost", "费用"), ("tickets", "票数"), ("rooms", "房间数"),
                               ("room_type", "房型"), ("stars", "星级"), ("end_day_offset", "结束日偏移"),
                               ("arrival_day_offset", "到达日偏移"), ("EndDayOffset", "到达日偏移"),
                               ("start_day", "开始日"), ("end_day", "结束日"),
                               ("departure_date", "出发日期"), ("arrival_date", "到达日期"),
                               ("duration_minutes", "时长分钟"), ("Duration", "时长"),
                               ("tags", "标签"), ("intensity", "活动强度"), ("facilities", "设施")):
                if key in activity:
                    details.append(f"{label}：{_text(activity[key])}")
            lines.append(f"| {d} | {_text(activity.get('start_time'))}–{_text(activity.get('end_time'))} | {_text(activity.get('type'))} | {'；'.join(details)} |")
    return "\n".join(lines)


def _normalize_records(records):
    """Deduplicate repeat runs without letting a method name determine ordering."""
    unique = {}
    queries = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Record {index} must be an object")
        for key in ("query_id", "fingerprint", "profile"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Record {index} requires nonempty {key}")
        if not isinstance(record.get("query"), dict) or not isinstance(record.get("plan"), dict):
            raise ValueError(f"Record {index} requires query and plan objects")
        if not isinstance(record["plan"].get("itinerary"), list):
            raise ValueError(f"Record {index} requires an itinerary list")
        qid = record["query_id"]
        group_id = record.get("query_group", qid)
        if not isinstance(group_id, str) or not group_id.strip():
            raise ValueError(f"Record {index} requires a nonempty query_group when supplied")
        query_signature = _digest(record["query"])
        if qid in queries and queries[qid] != (query_signature, group_id):
            raise ValueError(f"Conflicting user request or query_group for query_id {qid}")
        queries[qid] = (query_signature, group_id)
        key = (qid, record["profile"], record["fingerprint"])
        normalized = {
            "query_id": qid, "query_group": group_id, "query": record["query"], "plan": record["plan"],
            "fingerprint": record["fingerprint"], "profile": record["profile"],
            **{field: _score(record.get(field), field) for field in SCORE_FIELDS},
            "methods": [str(record.get("method", "unspecified"))],
        }
        if key in unique:
            existing = unique[key]
            if _digest(existing["plan"]) != _digest(normalized["plan"]) or any(existing[f] != normalized[f] for f in SCORE_FIELDS):
                raise ValueError(f"Conflicting plan or score for fingerprint {record['fingerprint']}")
            existing["methods"] = sorted(set(existing["methods"] + normalized["methods"]))
        else:
            unique[key] = normalized
    return [unique[key] for key in sorted(unique)]


def _sample_pairs(rows, count, rng):
    """At most count pairs, without enumerating a quadratic candidate pool."""
    total = len(rows) * (len(rows) - 1) // 2
    count = min(count, total)
    indices = set()
    while len(indices) < count:
        a, b = sorted(rng.sample(range(len(rows)), 2))
        indices.add((a, b))
    pairs = [(rows[a], rows[b]) for a, b in sorted(indices)]
    rng.shuffle(pairs)
    return pairs


def export_blind_pairs(records, output_dir, *, seed=20261111, max_pairs=40):
    """Write blinded itinerary comparisons and EMPTY human-label templates.

    A seed controls query sampling, group-level split, pair order and A/B order.
    Score profiles never cross within a pair. Repeated runs of the same plan
    are deduplicated. Optional query_group joins paraphrases of one original
    request for splitting/statistics; pairs still require identical query_id.
    Human reviewers should receive only share_files.
    """
    if isinstance(max_pairs, bool) or not isinstance(max_pairs, int) or max_pairs < 1:
        raise ValueError("max_pairs must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    rows = _normalize_records(list(records))
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["query_id"], row["profile"])].append(row)
    rng = random.Random(seed)
    pools = defaultdict(list)
    for (qid, _), group in sorted(grouped.items()):
        if len(group) >= 2:
            pools[qid].extend(_sample_pairs(group, max_pairs, rng))
    for pool in pools.values():
        rng.shuffle(pool)
    query_ids = sorted(pools)
    rng.shuffle(query_ids)
    # At least one pair per included query. Excess queries are sampled before
    # splitting so a validation query cannot disappear due to the pair cap.
    query_ids = query_ids[:max_pairs]
    picked = []
    while len(picked) < max_pairs:
        added = False
        for qid in query_ids:
            if pools[qid] and len(picked) < max_pairs:
                picked.append((qid, *pools[qid].pop()))
                added = True
        if not added:
            break
    query_groups = {row["query_id"]: row["query_group"] for row in rows if row["query_id"] in query_ids}
    split_ids = sorted(set(query_groups.values()))
    rng.shuffle(split_ids)
    if len(split_ids) < 2:
        group_split = {q: "pilot" for q in split_ids}
    else:
        validation_count = max(1, min(len(split_ids) - 1, round(len(split_ids) * 0.3)))
        group_split = {q: "validation" if i < validation_count else "dev" for i, q in enumerate(split_ids)}
    split_map = {qid: group_split[group] for qid, group in query_groups.items()}
    rng.shuffle(picked)
    public = ["# 行程人工盲选", "", "请根据每组用户需求比较两条完整行程。优先确认可执行性，再比较体验和明确偏好。",
              "", "在 labels.template.jsonl 中填写 winner：a、b、tie（差不多）或 uncertain（资料不足/无法判断），并填写 reason。",
              "未标注保持 null。不要为了区分而强迫选择；同一需求可能出现多个独立比较。", ""]
    pairs, labels = [], []
    for i, (qid, a, b) in enumerate(picked, 1):
        if rng.random() < 0.5:
            a, b = b, a
        pair_id = f"P{i:04d}"
        sides = {}
        for name, value in (("a", a), ("b", b)):
            sides[name] = {k: value[k] for k in ("fingerprint", "profile", "methods", *SCORE_FIELDS)}
        pairs.append({"pair_id": pair_id, "query_id": qid, "query_group": query_groups[qid], "split": split_map[qid], **sides})
        labels.append({"pair_id": pair_id, "winner": None, "reason": ""})
        public.extend([f"## {pair_id}", "", f"用户需求：{_text(_query_text(a['query']))}", "",
                       "### 行程 A", "", _render_plan(a["plan"]), "",
                       "### 行程 B", "", _render_plan(b["plan"]), "", "---", ""])
    only_pilot = len(group_split) < 2
    key = {
        "protocol": VERSION, "seed": seed, "max_pairs": max_pairs,
        "records_sha256": _digest(rows), "unique_plan_count": len(rows),
        "query_count": len(query_ids), "independent_query_count": len(group_split), "pair_count": len(pairs),
        "only_pilot": only_pilot, "query_split": split_map, "query_group_split": group_split, "pairs": pairs,
        "scope": "only_pilot" if only_pilot else "query_group_disjoint_dev_validation",
        "generalization_established": False,
    }
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=False)
    (out / "blind_review.md").write_text("\n".join(public), encoding="utf-8")
    _write_jsonl(out / "labels.template.jsonl", labels)
    _write_json(out / "private_key.json", key)
    readme = ("# 人工校准包\n\n仅把 blind_review.md 与 labels.template.jsonl 发给标注者。\n"
              "private_key.json 包含来源方法与自动评分，留给评测程序，不能用于盲选。\n\n"
              "所有 winner 初始为 null，没有生成或推测任何人工标签。填写后另存 labels.jsonl。\n"
              "修改权重只使用 dev；validation 中的需求应留到冻结评分后检查。同源改写按 query_group 一起划分。\n")
    if only_pilot:
        readme += "\n当前不足两个独立需求，只是 pilot，不能据此验证跨需求泛化或校准出通用权重。\n"
    (out / "README.md").write_text(readme, encoding="utf-8")
    return {k: v for k, v in key.items() if k not in {"pairs", "query_split", "query_group_split"}} | {
        "output_dir": str(out.resolve()), "share_files": ["blind_review.md", "labels.template.jsonl"],
        "private_key": "private_key.json", "labels_are_empty": True,
    }


def _prediction(pair, tie_margin):
    a = _score(pair["a"].get("composite_score"), "composite_score")
    b = _score(pair["b"].get("composite_score"), "composite_score")
    if a is None or b is None:
        return None
    return "a" if a - b > tie_margin else "b" if b - a > tie_margin else "tie"


def _summary(rows):
    total = len(rows)
    annotated = [r for r in rows if r["human"] is not None]
    usable = [r for r in rows if r["human"] in {"a", "b", "tie"}]
    comparable = [r for r in usable if r["predicted"] is not None]
    by_query = defaultdict(list)
    by_group = defaultdict(list)
    for row in comparable:
        correct = int(row["human"] == row["predicted"])
        by_query[row["query_id"]].append(correct)
        by_group[row["query_group"]].append(correct)
    winner_rows = [r for r in comparable if r["human"] in {"a", "b"}]
    tie_rows = [r for r in comparable if r["human"] == "tie"]
    correct = sum(r["human"] == r["predicted"] for r in comparable)
    return {
        "pair_count": total, "query_count": len({r["query_id"] for r in rows}),
        "independent_query_count": len({r["query_group"] for r in rows}),
        "annotated_count": len(annotated), "unlabeled_count": total - len(annotated),
        "annotation_coverage": len(annotated) / total if total else None,
        "uncertain_count": sum(r["human"] == "uncertain" for r in rows),
        "human_tie_count": sum(r["human"] == "tie" for r in rows),
        "usable_label_count": len(usable), "comparable_count": len(comparable),
        "missing_prediction_count": len(usable) - len(comparable),
        "prediction_coverage_on_usable_labels": len(comparable) / len(usable) if usable else None,
        "correct_count": correct,
        "accuracy": statistics.mean(statistics.mean(v) for v in by_group.values()) if by_group else None,
        "accuracy_definition": "equal-query-group mean of agreement; human tie included; uncertain/unlabeled/missing prediction excluded and counted",
        "pair_accuracy": correct / len(comparable) if comparable else None,
        "human_winner_pair_accuracy": sum(r["human"] == r["predicted"] for r in winner_rows) / len(winner_rows) if winner_rows else None,
        "human_tie_pair_accuracy": sum(r["predicted"] == "tie" for r in tie_rows) / len(tie_rows) if tie_rows else None,
        "scorer_tie_against_human_winner_count": sum(r["predicted"] == "tie" for r in winner_rows),
        "query_accuracies": {q: {"accuracy": statistics.mean(v), "comparable_count": len(v)} for q, v in sorted(by_query.items())},
        "query_group_accuracies": {q: {"accuracy": statistics.mean(v), "comparable_count": len(v)} for q, v in sorted(by_group.items())},
    }


def evaluate_labels(key_path, labels_path, *, tie_margin=3):
    """Assess ONLY submitted human labels; empty templates yield accuracy=null."""
    if isinstance(tie_margin, bool) or not isinstance(tie_margin, (int, float)) or not math.isfinite(tie_margin) or tie_margin < 0:
        raise ValueError("tie_margin must be a finite nonnegative number")
    key = json.loads(Path(key_path).read_text(encoding="utf-8-sig"))
    if key.get("protocol") != VERSION or not isinstance(key.get("pairs"), list):
        raise ValueError("Unsupported or invalid calibration key")
    pairs = {}
    split_by_query = {}
    split_by_group = {}
    groups_by_query = {}
    for pair in key["pairs"]:
        pair_id = pair.get("pair_id")
        if not isinstance(pair_id, str) or pair_id in pairs:
            raise ValueError("Invalid or duplicate pair_id in private key")
        if pair.get("split") not in {"pilot", "dev", "validation"}:
            raise ValueError("Invalid split in private key")
        qid = pair.get("query_id")
        if not isinstance(qid, str):
            raise ValueError("Invalid query_id in private key")
        if qid in split_by_query and split_by_query[qid] != pair["split"]:
            raise ValueError("The same query appears in multiple splits")
        split_by_query[qid] = pair["split"]
        group = pair.get("query_group", qid)  # Legacy keys used one query per group.
        if not isinstance(group, str) or not group.strip():
            raise ValueError("Invalid query_group in private key")
        if group in split_by_group and split_by_group[group] != pair["split"]:
            raise ValueError("The same query_group appears in multiple splits")
        if qid in groups_by_query and groups_by_query[qid] != group:
            raise ValueError("The same query_id appears in multiple query groups")
        split_by_group[group] = pair["split"]
        groups_by_query[qid] = group
        if pair.get("a", {}).get("profile") != pair.get("b", {}).get("profile"):
            raise ValueError("Cannot compare scores from different profiles")
        pairs[pair_id] = pair
    labels = {}
    for label in _read_jsonl(labels_path):
        pair_id = label.get("pair_id")
        if not isinstance(pair_id, str) or pair_id not in pairs:
            raise ValueError(f"Unknown pair_id in human labels: {pair_id!r}")
        if pair_id in labels:
            raise ValueError(f"Duplicate human label for {pair_id}")
        if "winner" not in label or not isinstance(label["winner"], (str, type(None))) or label["winner"] not in WINNERS:
            raise ValueError(f"Invalid winner for {pair_id}; expected null/a/b/tie/uncertain")
        if not isinstance(label.get("reason", ""), str):
            raise ValueError(f"reason must be text for {pair_id}")
        labels[pair_id] = label
    rows = []
    for pair_id, pair in pairs.items():
        label = labels.get(pair_id, {})
        rows.append({"pair_id": pair_id, "query_id": pair["query_id"], "query_group": pair.get("query_group", pair["query_id"]), "split": pair["split"],
                     "human": label.get("winner"), "predicted": _prediction(pair, tie_margin),
                     "reason": label.get("reason", "")})
    only_pilot = len(split_by_group) < 2
    return {
        "protocol": VERSION, "tie_margin": tie_margin,
        "key_sha256": hashlib.sha256(Path(key_path).read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(Path(labels_path).read_bytes()).hexdigest(),
        "query_count": len(split_by_query), "independent_query_count": len(split_by_group),
        "only_pilot": only_pilot, "generalization_established": False,
        "scope": "only_pilot" if only_pilot else "query_group_disjoint_dev_validation",
        "overall": _summary(rows),
        "splits": {name: _summary([r for r in rows if r["split"] == name]) for name in ("pilot", "dev", "validation")},
        "pairs": rows,
        "note": "No human labels are inferred from model scores. A single independent query group is diagnostic only; disjoint groups alone do not establish generalization.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Create a blinded human review package")
    export.add_argument("--records", required=True, type=Path)
    export.add_argument("--out", required=True, type=Path)
    export.add_argument("--seed", type=int, default=20261111)
    export.add_argument("--max-pairs", type=int, default=40)
    assess = commands.add_parser("assess", help="Evaluate actual human labels")
    assess.add_argument("--key", required=True, type=Path)
    assess.add_argument("--labels", required=True, type=Path)
    assess.add_argument("--tie-margin", type=float, default=3)
    assess.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "export":
        result = export_blind_pairs(_read_jsonl(args.records), args.out, seed=args.seed, max_pairs=args.max_pairs)
    else:
        result = evaluate_labels(args.key, args.labels, tie_margin=args.tie_margin)
        if args.out:
            # Preserve prior assessments; a different label set gets a new path.
            with args.out.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
