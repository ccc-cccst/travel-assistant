"""Measure a conservative transport-budget screen over labeled local queries."""

import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import re
import sys
from time import perf_counter

import pandas as pd

from speed_study import DATASET, REFERENCE


BUDGET = re.compile(
    r"total_cost\s*=\s*0\s+for activity in allactivities\(plan\):\s*"
    r"total_cost\s*\+=\s*activity_cost\(activity\)\s*"
    r"(?:total_cost\s*\+=\s*)?\+?\s*innercity_transport_cost\(activity_transports\(activity\)\)\s*"
    r"result\s*=\s*\(total_cost\s*<=\s*(people_count\(plan\)\s*\*\s*)?(\d+(?:\.\d+)?)\)\s*"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = REFERENCE / "sources/shorttrip"
    sys.path.insert(0, str(source))
    from chinatravel.environment.tools.intercity_transport.apis import IntercityTransport

    db = IntercityTransport(lang="zh")
    prices = {}

    def costs(start, end):
        route = start, end
        if route not in prices:
            all_prices = []
            for kind in ("train", "airplane"):
                frame = db.select(start, end, kind)
                if frame is None or frame.empty:
                    continue
                arrivals = frame["EndTime"].astype(str).str.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d")
                available = pd.to_numeric(frame.loc[arrivals, "Cost"], errors="coerce")
                all_prices.extend(v for v in available if math.isfinite(v) and v >= 0)
            prices[route] = all_prices
        return prices[route]

    rows = []
    elapsed = 0.
    for path in sorted(DATASET.glob("*.json")):
        query = json.loads(path.read_text(encoding="utf-8"))
        limits = [float(match[2]) * (query["people_number"] if match[1] else 1)
                  for rule in query["hard_logic_py"] if (match := BUDGET.fullmatch(rule))]
        budget = min(limits, default=None)
        start, end = query["start_city"], query["target_city"]
        row = {"case": path.stem, "start": start, "end": end, "days": query["days"],
               "people": query["people_number"], "budget": budget, "ticket_floor": None,
               "outbound_count": None, "unaffordable_outbound": None, "status": "no_known_budget"}
        if budget is not None:
            began = perf_counter()
            try:
                outbound, inbound = costs(start, end), costs(end, start)
            except KeyError:
                outbound, inbound = [], []
            row["outbound_count"] = len(outbound)
            if outbound and inbound:
                cheapest_return = min(inbound)
                floor = (min(outbound) + cheapest_return) * query["people_number"]
                skipped = sum((cost + cheapest_return) * query["people_number"] > budget for cost in outbound)
                row.update(ticket_floor=floor, unaffordable_outbound=skipped,
                           status="all_over_budget" if skipped == len(outbound) else
                           "some_over_budget" if skipped else "none_over_budget")
                assert (skipped == len(outbound)) == (floor > budget)
            else:
                row["status"] = "no_priced_route"
            elapsed += perf_counter() - began
        rows.append(row)
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out / "ticket_floor.csv").open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {"queries": len(rows), "screen_seconds": elapsed, "by_tier": {}}
    for tier in ("easy", "medium", "human"):
        subset = [row for row in rows if row["case"].startswith(tier)]
        summary["by_tier"][tier] = dict(Counter(row["status"] for row in subset))
    (args.out / "ticket_floor.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
