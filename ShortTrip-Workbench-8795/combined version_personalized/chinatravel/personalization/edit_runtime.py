"""Reuse decoded tables and native tool imports inside one pinned edit process."""

from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import os
from pathlib import Path
import sys

from chinatravel.environment.sandbox.__main__ import configuration

from .world_facts import WorldFacts


@contextmanager
def decoded_intercity_tables(database):
    """The native APIs build new selectors; reuse decoding and give each its own copy."""
    import pandas as pd

    read_json = pd.read_json
    train_root = database / "intercity_transport" / "train"
    flight_file = database / "intercity_transport" / "airplane.jsonl"

    @lru_cache(maxsize=160)
    def decoded(path, flight):
        options = {"lines": True, "keep_default_dates": False} if flight else {}
        frame = read_json(path, **options)
        structured = tuple(col for col in frame.select_dtypes(include=["object"])
                           if any(isinstance(value, (dict, list, set)) for value in frame[col]))
        return frame, structured

    def read(path_or_buf, *args, **kwargs):
        if not args and isinstance(path_or_buf, (str, Path)):
            path = Path(os.path.abspath(path_or_buf))
            train = path.parent == train_root and path.suffix == ".json" and not kwargs
            flight = path == flight_file and kwargs == {"lines": True, "keep_default_dates": False}
            if train or flight:
                original, structured = decoded(path, flight)
                owned = original.copy(deep=True)
                for column in structured:
                    owned[column] = original[column].map(deepcopy)
                return owned
        return read_json(path_or_buf, *args, **kwargs)

    pd.read_json = read
    try:
        yield
    finally:
        pd.read_json = read_json


@contextmanager
def pinned_runtime_imports(sandbox):
    """Keep imports after activation when the exact same engine is still selected."""
    adapter = sys.modules.get(type(sandbox).__module__)
    activate = getattr(adapter, "_activate", None)
    if not callable(activate):
        yield
        return
    selected = sandbox.root.resolve()
    tools = ("chinatravel.environment.tools.transportation.apis",
             "chinatravel.environment.tools.intercity_transport.apis",
             "chinatravel.symbol_verification.commonsense_constraint")
    activated = False

    def activate_selected(root):
        nonlocal activated
        if root == selected and activated and sys.path[0] == str(selected):
            origins = [getattr(sys.modules[name], "__file__", "") for name in tools if name in sys.modules]
            if origins and all(origin and Path(origin).is_relative_to(selected) for origin in origins):
                return
        activate(root)
        activated = root == selected

    adapter._activate = activate_selected
    try:
        yield
    finally:
        adapter._activate = activate


@contextmanager
def preview_environment(data):
    """Initialize the same verified data as the ordinary planner, in this process only."""
    configured = configuration(Path(data))
    os.environ.update(configured)
    with decoded_intercity_tables(Path(configured["CHINATRAVEL_DATABASE_ROOT"])):
        from chinatravel.environment.world_env import WorldEnv
        facts = WorldFacts.configured(data)

        @lru_cache(maxsize=3)
        def world(cities):
            return WorldEnv(lang="zh", cities=cities, preload_transport=False)

        def world_for(origin, destination):
            return world(frozenset((origin, destination)))

        with pinned_runtime_imports(facts.sandbox):
            yield world_for, facts
