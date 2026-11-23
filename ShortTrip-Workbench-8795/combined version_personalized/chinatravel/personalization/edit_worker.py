"""Run itinerary previews sequentially in one isolated snapshot environment."""

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

from .cli import fresh, read, save
from .edit_runtime import preview_environment
from .lessons import describe_exception


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    boot = perf_counter()
    with preview_environment(args.data) as (world_for, facts):
        serve(world_for, facts, boot)


def serve(world_for, facts, boot):
    from .itinerary_edit import edit_preview, prepare_snapshot_routes
    worker_id = uuid4().hex
    initialization = perf_counter() - boot
    for line in sys.stdin:
        task = json.loads(line)
        if task.get("warm"):
            pair = task["warm"]
            try:
                prepare_snapshot_routes(pair, world_for(pair["start_city"], pair["target_city"]), facts)
            except Exception as error:
                print(f"Preview prewarm failed: {type(error).__name__}", file=sys.stderr)
            continue
        folder = Path(task["folder"])
        output = fresh(folder / "run")
        started = perf_counter()
        source = Path(task["source"])
        failed = False
        env = None
        try:
            snapshot = read(source / "WORLD_FACTS.json")
            env = world_for(snapshot["origin"], snapshot["destination"])
            edit_preview(source, output, task["candidate"],
                         read(folder / "operation.json"), env, facts)
        except Exception as error:
            failed = True
            save(output / "STATUS.json", {**describe_exception(error, "itinerary_edit"),
                                          "protocol": "integrated_planning_v1"})
        finally:
            if env is not None:
                env.results.clear()
        save(folder / "WORKER_TIMING.json", {"mode": "snapshot_worker", "worker":worker_id,
             "initialization_seconds":round(initialization, 4), "preview_seconds":round(perf_counter() - started, 4)})
        if failed:
            return


if __name__ == "__main__":
    main()
