"""Capture search starts from local benchmark constraints and recommendation fallbacks."""

import argparse
import contextlib
from pathlib import Path
import time

from .run import frozen, PROJECT
from chinatravel.environment.world_env import WorldEnv
from experiments.evaluation.common import file_digest, read_json


# Symbolic counterparts of the benchmark constraints used by prefix pruning.
# Complete-plan validation still uses the benchmark's original programs.
HARD = {
    "easy_1": ("intercity_transport=={'train'}",),
    "easy_2": ("transport_type<={'metro'}",),
    "easy_3": ("{'富春山居号游船'}<=attraction_names",),
    "easy_4": ("{'久久日本料理'}<=restaurant_names",),
    "easy_5": ("cost<=1300",),
    "human_1": ("cost<=3000", "{'武汉大学'}<=attraction_names", "{'湖北菜'}<=food_type"),
    "human_2": ("cost<=3000", "{'历史古迹','博物馆/纪念馆'}<=spot_type"),
    "human_3": ("return_start_time<='19:00'", "intercity_transport=={'train'}",
                "{'大学校园'}<=spot_type"),
    "human_4": ("cost<=9000", "{'自然风光','历史古迹'}<=spot_type"),
    "human_5": ("cost<=2000", "intercity_transport=={'train'}",
                "{'浙江大学(玉泉校区)','西湖风景名胜区'}<=attraction_names or "
                "{'浙江大学(之江校区)','西湖风景名胜区'}<=attraction_names"),
}


class RecommendationFallback:
    name = "recommendation_fallback"

    def __call__(self, *args, **kwargs):
        # Empty output activates the production parsers' deterministic fallbacks.
        return ""


class OfflineCapture(frozen.CaptureAgent):
    def select_next_poi_type(self, *args, **kwargs):
        return frozen.PrefixProbeAgent.select_next_poi_type(self, *args, **kwargs)


def capture(case, directory):
    directory.mkdir(parents=True, exist_ok=False)
    query_path = PROJECT / "experiments/c_only/queries" / f"{case}.json"
    query = read_json(query_path)
    query["hard_logic"] = [f"days=={query['days']}", f"people_number=={query['people_number']}",
                           f"tickets=={query['people_number']}", "taxi_cars==1", *HARD[case]]
    agent = OfflineCapture(group="C", env=WorldEnv(lang="zh"), backbone_llm=RecommendationFallback(),
                           cache_dir=str(directory / "cache"), debug=False, lang="zh",
                           wall_limit_seconds=30)
    agent.activity_policy = "rules"
    agent.soft_started = time.monotonic()
    with (directory / "prepare.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                _, diagnostic = agent.symbolic_search(query)
            except frozen.SnapshotReady:
                state = agent.capture
            else:
                raise RuntimeError(f"First attraction decision not reached: {diagnostic}")
            agent.ranking_restaurants(state["plan"], state["poi_plan"], state["current_day"],
                                      state["current_time"], state["current_position"],
                                      agent.intercity_with_hotel_cost)
    state["agent"] = {name: frozen.freeze(getattr(agent, name)) for name in frozen.STATE_FIELDS}
    state["memory"] = {name: frozen.freeze(frame.to_dict(orient="split"))
                       for name, frame in agent.memory.items()}
    frozen.save(directory / "frozen.json", {
        "schema_version": 1, "case": case, "uid": query["uid"],
        "scope": "benchmark hard constraints, recommendation fallback, fixed pre-first-attraction state",
        "oracle_in_planning": True, "state": state, "prepare_api_calls": 0,
        "source_sha256": {name: file_digest(PROJECT / name) for name in frozen.SOURCES},
        "source_query_sha256": file_digest(query_path),
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results = {}
    for case in HARD:
        try:
            capture(case, args.out / case)
            results[case] = {"status": "frozen"}
        except Exception as error:
            results[case] = {"status": "preparation_failed",
                             "error": f"{type(error).__name__}: {error}"}
        print(case, results[case], flush=True)
        frozen.save(args.out / "preparation.json", results)


if __name__ == "__main__":
    main()
