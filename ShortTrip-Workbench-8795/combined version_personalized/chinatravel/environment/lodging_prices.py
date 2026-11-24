"""Read estimated lodging price intervals linked to the source CSV."""

import csv
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def estimate_ranges(path):
    if not Path(path).is_file():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as source:
        return {
            row["name"]: {"price_low": float(row["price_low"]), "price_high": float(row["price_high"])}
            for row in csv.DictReader(source)
            if row["price_low"] and row["price_high"]
        }
