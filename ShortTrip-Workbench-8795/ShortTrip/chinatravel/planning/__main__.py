"""Run a plan from JSON or open the local trip planning interface."""

import argparse
import json
import os
from pathlib import Path
import sys


def configure_data(profile):
    if profile == "expanded11":
        from chinatravel.environment.sandbox.__main__ import configuration
        from chinatravel.environment.sandbox.install import DEFAULT_INSTALL
        os.environ.update(configuration(DEFAULT_INSTALL))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("expanded11", "current"), default="expanded11")
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        configure_data(args.profile)
        from .service import PlanningService
        service = PlanningService()
        if args.serve:
            from .web import serve
            serve(service, args.port, args.seconds)
            return 0
        if args.input is None:
            parser.error("请指定 --input 需求.json 或 --serve")
        query = json.loads(args.input.read_text(encoding="utf-8-sig"))
        result = service.generate(query, max_seconds=args.seconds)
    except (OSError, ValueError, TypeError) as error:
        result = {"status": "invalid_request", "plan": None, "issues": [{"message": str(error)}]}
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(text + "\n", encoding="utf-8")
        temporary.replace(args.output)
    print(text)
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    sys.exit(main())
