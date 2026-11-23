"""Interleave extra baseline/instrumented default-cap runs after observed timing drift."""
import argparse
import time
from pathlib import Path
from run import timed_search, runner, save

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
args.out = args.out.resolve()
target = args.out / "SUPPLEMENTAL.json"
if target.exists():
    raise FileExistsError(target)
reference = runner.read(args.out / "COMPARISON.json")
rows = []
for repeat in (2, 3):
    for variant in ("original", "rank_fixed"):
        for measured in ((False, True) if repeat == 2 else (True, False)):
            cpu_started = time.process_time()
            row = timed_search(args, variant, 20, repeat if measured else repeat + 2, measured)
            row["driver_cpu_seconds"] = time.process_time() - cpu_started
            prior = runner.read(args.out / "baseline_r1_c20" / variant / "timing.json")
            keys = ("candidate_sequence_sha256", "dfs_entries", "stop_reason", "counters", "prune_counts")
            if any(row[k] != prior[k] for k in keys):
                raise ValueError("Supplemental run changed search outputs/counters")
            rows.append(row)
save(target, {"reason": "Wall time drift in initial matrix; interleave repeats and record process CPU time",
              "measurement_source_sha256": runner.digest(Path(__file__)), "rows": rows})
print(f"Completed {len(rows)} supplemental searches; outputs and counters unchanged.")
