"""Small deterministic IO helpers; no planner, model or environment setup."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

PROJECT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PLAN_FIELDS = ("people_number", "start_city", "target_city", "itinerary")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return clean_json(value.item())
    return value


def canonical(value):
    return json.dumps(clean_json(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan_fingerprint(plan):
    return digest({k: plan[k] for k in PLAN_FIELDS if k in plan})


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(clean_json(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]


def merged(base, override):
    result = dict(base)
    for key, value in (override or {}).items():
        result[key] = merged(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
