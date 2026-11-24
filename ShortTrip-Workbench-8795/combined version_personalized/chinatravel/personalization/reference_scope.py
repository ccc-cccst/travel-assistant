"""Scope ShortTrip reference planning to the two cities added by the V20 snapshot."""

import os


REFERENCE_CITIES = frozenset(("西安", "厦门"))
HISTORICAL_RAIL_FARE = "12306_historical_benchmark_reference"


def reference_cities():
    return REFERENCE_CITIES if os.environ.get("CHINATRAVEL_PROFILE") == "expanded12" else frozenset()


def reference_route(origin, destination):
    return bool(reference_cities().intersection((origin, destination)))
