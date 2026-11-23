"""Independent, offline plan validation with explicit evaluator uncertainty.

The existing ChinaTravel validators read local databases and execute their own
restricted constraint DSL. This module never imports a planner or model client.
Only reviewed dataset constraints belong in ``query['hard_logic_py']``.
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from functools import lru_cache
import importlib
import io
import json
import math
from pathlib import Path
import sys
import threading


PROJECT = Path(__file__).resolve().parents[2]
COMMONSENSE_NAMES = (
    "Is_activity_grounded", "Is_intercity_transport_correct",
    "Is_attractions_correct", "Is_hotels_correct", "Is_restaurants_correct",
    "Is_transport_correct", "Is_time_correct", "Is_space_correct",
)
# Upstream validators switch module-global language tools. Serialize calls made
# through this wrapper; use separate processes for parallel evaluation.
_LOCK = threading.RLock()


@lru_cache(maxsize=1)
def _schema_validator():
    from jsonschema import Draft7Validator
    schema_path = PROJECT / "chinatravel/evaluation/output_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def _ensure_import_path():
    project = str(PROJECT)
    if project not in sys.path:
        sys.path.insert(0, project)


@lru_cache(maxsize=1)
def _commonsense_backend():
    _ensure_import_path()
    return importlib.import_module("chinatravel.symbol_verification.commonsense_constraint")


@lru_cache(maxsize=1)
def _hard_backend():
    _ensure_import_path()
    return importlib.import_module("chinatravel.symbol_verification.hard_constraint")


@lru_cache(maxsize=1)
def _c_backend():
    _ensure_import_path()
    # rules.ticket_interval uses a legacy sibling ``from catalog import lookup``.
    # Keep this directory last so unrelated installed modules are not shadowed.
    sibling = str(PROJECT / "experiments/c_only")
    if sibling not in sys.path:
        sys.path.append(sibling)
    return importlib.import_module("experiments.c_only.rules")


def _exception(exc):
    return {"type": type(exc).__name__, "message": str(exc)}


def _capture(call):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = call()
    logs = {"stdout": stdout.getvalue()[-4000:], "stderr": stderr.getvalue()[-4000:]}
    return result, {k: v for k, v in logs.items() if v}


def _summarize(checks):
    violations = [c for c in checks if c["status"] == "fail"]
    errors = [c for c in checks if c["status"] == "unknown"]
    status = "invalid" if violations else "unknown" if errors else "valid"
    return {
        "status": status, "checks": checks, "violations": violations,
        "errors": errors, "validator_version": "shorttrip-validation-v0",
    }


def _clock_errors(plan):
    """The repository JSON schema checks HH:MM shape, but not clock bounds."""
    errors = []
    for d, day in enumerate(plan["itinerary"]):
        for a, activity in enumerate(day["activities"]):
            for name, row in [(f"{d}/{a}", activity)] + [
                (f"{d}/{a}/transport/{t}", leg)
                for t, leg in enumerate(activity.get("transports", []))
            ]:
                for field in ("start_time", "end_time"):
                    value = row.get(field)
                    if value is None:
                        continue  # requiredness is owned by the JSON schema.
                    try:
                        h, m = map(int, value.split(":"))
                        if not (0 <= h <= 24 and 0 <= m < 60 and (h != 24 or m == 0)):
                            raise ValueError("clock outside 00:00..24:00")
                    except (ValueError, TypeError, AttributeError):
                        errors.append({"activity": name, "field": field, "value": value})
    return errors


def _nonfinite_paths(value, path=()):
    if isinstance(value, float) and not math.isfinite(value):
        return [list(path)]
    if isinstance(value, dict):
        return [p for key, item in value.items() for p in _nonfinite_paths(item, (*path, key))]
    if isinstance(value, list):
        return [p for i, item in enumerate(value) for p in _nonfinite_paths(item, (*path, i))]
    return []


def _check_hard_expression(expression, plan, backend):
    if not isinstance(expression, str) or not expression.strip():
        return "unknown", {"reason": "constraint_must_be_nonempty_dsl"}
    # Upstream catches DSL exceptions and otherwise returns False. Verbose mode
    # exposes that distinction; run each expression separately to preserve it.
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        flags = backend.evaluate_constraints_py([expression], deepcopy(plan), verbose=True)
    output = stdout.getvalue()
    logs = {k: v for k, v in {"stdout": output[:4000], "stderr": stderr.getvalue()[:4000]}.items() if v}
    if "Error evaluating constraint" in output:
        return "unknown", {"reason": "dsl_evaluation_error", "diagnostics": logs}
    if not isinstance(flags, list) or len(flags) != 1 or not isinstance(flags[0], bool):
        return "unknown", {"reason": "invalid_constraint_evaluator_result"}
    return ("pass" if flags[0] else "fail"), {"result": flags[0], **logs}


def validate_plan(plan, query, *, config=None, requirements=None):
    """Validate a complete itinerary independently of its planning-time flags.

    ``config`` accepts ``protocol`` (only ``single_destination_roundtrip`` is
    supported) and ``language`` (zh/en; inferred when absent). It also accepts a
    containing application's config with a nested ``validation`` object.
    The explicit time_policy selects strict (default) or flexible meal windows.
    Requirements with kind=hard can provide method=dsl and an ``expression``.
    Missing or unsupported hard checks produce unknown, never a silent pass.
    """
    checks = []

    def add(name, status, details=None):
        checks.append({"name": name, "status": status, "details": details or {}})

    if config is None:
        config = {}
    if not isinstance(config, dict):
        add("configuration", "unknown", {"reason": "config_must_be_object"})
        return _summarize(checks)
    config = config.get("validation", config)
    if not isinstance(config, dict):
        add("configuration", "unknown", {"reason": "validation_config_must_be_object"})
        return _summarize(checks)

    try:
        schema_errors = list(_schema_validator().iter_errors(plan))
        if schema_errors:
            add("schema", "fail", {"errors": [
                {"path": list(e.absolute_path), "message": e.message}
                for e in schema_errors
            ]})
            return _summarize(checks)
        add("schema", "pass")
    except Exception as exc:
        add("schema", "unknown", _exception(exc))
        return _summarize(checks)

    nonfinite = _nonfinite_paths(plan)
    if nonfinite:
        add("finite_numbers", "fail", {"paths": nonfinite})
        return _summarize(checks)
    add("finite_numbers", "pass")

    clock_errors = _clock_errors(plan)
    if clock_errors:
        add("clock_bounds", "fail", {"errors": clock_errors})
        return _summarize(checks)
    add("clock_bounds", "pass")

    protocol = config.get("protocol", "single_destination_roundtrip")
    if protocol != "single_destination_roundtrip":
        add("protocol", "unknown", {"reason": "unsupported_protocol", "protocol": protocol})
        return _summarize(checks)
    add("protocol", "pass", {"protocol": protocol})

    if not isinstance(query, dict):
        add("query", "unknown", {"reason": "query_must_be_object"})
        return _summarize(checks)
    missing = [key for key in ("start_city", "target_city", "days", "people_number")
               if query.get(key) is None]
    if missing:
        add("query", "unknown", {"reason": "missing_query_metadata", "fields": missing})
        return _summarize(checks)
    if (not isinstance(query["start_city"], str) or not isinstance(query["target_city"], str)
            or isinstance(query["days"], bool) or not isinstance(query["days"], int)
            or query["days"] < 1 or isinstance(query["people_number"], bool)
            or not isinstance(query["people_number"], int) or query["people_number"] < 1):
        add("query", "unknown", {"reason": "invalid_query_metadata"})
        return _summarize(checks)
    identity = {
        "people_number": plan["people_number"] == query["people_number"],
        "start_city": plan["start_city"] == query["start_city"],
        "target_city": plan["target_city"] == query["target_city"],
        "days": len(plan["itinerary"]) == query["days"],
        "day_indices": [day["day"] for day in plan["itinerary"]] == list(range(1, query["days"] + 1)),
    }
    add("query_identity", "pass" if all(identity.values()) else "fail", identity)

    from chinatravel.soft_constraints.time_preferences import time_policy
    try:
        policy=time_policy(config.get('time_policy',query.get('time_policy','strict')))
    except ValueError as exc:
        add('time_policy','unknown',_exception(exc))
        return _summarize(checks)
    query=deepcopy(query)
    query['time_policy']=policy.name
    add('time_policy','pass',{'name':policy.name})

    with _LOCK:
        try:
            backend, _ = _capture(_commonsense_backend)
            language = config.get("language") or backend._infer_lang(query)
            _capture(lambda: backend._set_tool_lang(language))
        except Exception as exc:
            backend = None
            language = config.get("language") or "zh"
            add("commonsense_resources", "unknown", _exception(exc))
        if backend is not None:
            for name in COMMONSENSE_NAMES:
                try:
                    (table, info), logs = _capture(lambda name=name: getattr(backend, name)(
                        deepcopy(query), deepcopy(plan), verbose=False))
                    if len(table.index) != 1 or not len(table.columns):
                        raise ValueError("Commonsense evaluator returned an empty/invalid table")
                    values = {str(k): float(v) for k, v in table.iloc[0].items()}
                    if any(not math.isfinite(v) or v < 0 for v in values.values()):
                        raise ValueError("Commonsense evaluator returned invalid counts")
                    add(name, "pass" if all(v == 0 for v in values.values()) else "fail",
                        {"counts": values, "messages": info, **logs})
                except Exception as exc:
                    add(name, "unknown", _exception(exc))

        expressions = query.get("hard_logic_py")
        if expressions is None:
            add("dataset_hard", "unknown", {"reason": "hard_constraints_not_supplied"})
            expressions = []
        elif not isinstance(expressions, list):
            add("dataset_hard", "unknown", {"reason": "hard_constraints_must_be_list"})
            expressions = []
        elif not expressions:
            add("dataset_hard", "not_applicable", {"reason": "explicit_empty_constraint_list"})
        hard_jobs = [(f"dataset_hard/{i}", value) for i, value in enumerate(expressions)]
        if requirements is not None:
            if not isinstance(requirements, dict) or not isinstance(requirements.get("items", []), list):
                add("requirements", "unknown", {"reason": "invalid_requirements_schema"})
            else:
                for i, item in enumerate(requirements.get("items", [])):
                    if not isinstance(item, dict):
                        add(f"requirement/{i}", "unknown", {"reason": "invalid_requirement"})
                        continue
                    if item.get("kind") != "hard":
                        continue
                    name = f"requirement/{item.get('id', i)}"
                    if requirements.get("reviewed") is not True:
                        add(name, "unknown", {"reason": "hard_requirement_unreviewed"})
                    elif item.get("method") != "dsl":
                        add(name, "unknown", {"reason": "unsupported_hard_requirement_method"})
                    else:
                        hard_jobs.append((name, item.get("expression")))
        if hard_jobs:
            try:
                hard, _ = _capture(_hard_backend)
                # Concept functions are language-sensitive, too.
                _capture(lambda: hard._set_tool_lang(language))
            except Exception as exc:
                hard = None
                add("hard_constraint_resources", "unknown", _exception(exc))
            if hard is not None:
                for name, expression in hard_jobs:
                    try:
                        status, details = _check_hard_expression(expression, plan, hard)
                        add(name, status, details)
                    except Exception as exc:
                        add(name, "unknown", _exception(exc))

        try:
            c_rules, _ = _capture(_c_backend)
            result, logs = _capture(lambda: c_rules.audit(deepcopy(plan), query["days"],policy.name))
            if not isinstance(result, dict) or not isinstance(result.get("all_pass"), bool):
                raise ValueError("C audit returned invalid output")
            add("C_audit", "pass" if result["all_pass"] else "fail", {**result, **logs})
        except Exception as exc:
            add("C_audit", "unknown", _exception(exc))
    return _summarize(checks)
