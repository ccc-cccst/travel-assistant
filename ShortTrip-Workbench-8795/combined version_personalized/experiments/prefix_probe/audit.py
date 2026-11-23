"""Post-hoc dataset hard-constraint audit; never supplies oracle data to planning."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import PROJECT, digest, save
from chinatravel.symbol_verification.hard_constraint import evaluate_constraints_py
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.out.resolve()
    frozen = json.loads((root / "frozen.json").read_text(encoding="utf-8"))
    source = PROJECT / "experiments/c_only/queries" / (frozen["case"] + ".json")
    if digest(source) != frozen["source_query_sha256"]:
        raise ValueError("Evaluation source query changed")
    full_query = json.loads(source.read_text(encoding="utf-8"))
    results = []
    for path in sorted(root.glob("*/*/candidates.json")):
        candidates = json.loads(path.read_text(encoding="utf-8"))
        passed = 0
        failures = []
        for candidate in candidates:
            try:
                flags = [bool(v) for v in evaluate_constraints_py(
                    full_query["hard_logic_py"], candidate["plan"],
                )]
                if flags and all(flags):
                    passed += 1
                else:
                    failures.append({"ordinal": candidate["ordinal"], "flags": flags})
            except Exception as exc:
                failures.append({
                    "ordinal": candidate["ordinal"], "error": type(exc).__name__,
                    "message": str(exc),
                })
        results.append({
            "run": str(path.parent.relative_to(root)),
            "candidate_count": len(candidates), "dataset_hard_pass": passed,
            "failures": failures, "candidates_sha256": digest(path),
        })
    if not results:
        raise ValueError("No candidates files found")
    record = {
        "evaluation_only": True, "oracle_supplied_to_planner": False,
        "source_query_sha256": digest(source),
        "note": "Dataset hard constraints only; C audit and original commonsense checks are reported separately.",
        "runs": results,
    }
    save(root / "DATASET_HARD_AUDIT.json", record)
    for result in results:
        print(f"{result['run']}: {result['dataset_hard_pass']}/{result['candidate_count']} dataset hard constraints passed")
    if any(result["failures"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
