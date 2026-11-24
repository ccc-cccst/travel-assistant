"""Install, inspect or run ShortTrip with a verified data profile."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .install import DATABASE, DEFAULT_INSTALL, PROJECT, SOURCE, checked_file, install, verify_profile
from .v20 import COMMIT as V20_COMMIT, PROFILE as V20_PROFILE, install_v20, verify as verify_v20


_BUNDLED_DATA = PROJECT / "artifacts" / "data"
INSTALLS = (_BUNDLED_DATA if (_BUNDLED_DATA / V20_COMMIT[:7] / "install.json").is_file()
            else PROJECT.parent / "ShortTrip" / "artifacts" / "data")


def default_installation():
    current = INSTALLS / V20_COMMIT[:7]
    return current if current.is_dir() else INSTALLS / SOURCE["commit"][:7]


def configuration(installation):
    installation = installation.resolve()
    v20 = installation / "sandbox_profiles" / V20_PROFILE
    if installation.name == V20_COMMIT[:7] or v20.exists():
        verify_v20(v20)
        base = configuration(installation.with_name(SOURCE["commit"][:7]))
        return {
            **base,
            "CHINATRAVEL_PROFILE": "expanded12",
            "CHINATRAVEL_DATABASE_ROOT": str(v20 / "database"),
            "CT_SANDBOX_RUNTIME_DIR": str(v20 / "runtime"),
            "CT_SANDBOX_ENGINE_ROOT": str(v20 / "engine"),
            "CT_XIAMEN_DISCOVERY_ONLY": "1",
            "CT_XIAMEN_HISTORICAL_FARES": "1",
            "CT_FREE_ATTRACTION_ASSUMPTION": "0",
        }
    profile = installation / "sandbox_profiles" / SOURCE["profile"]
    verify_profile(profile)
    catalog = checked_file(installation, "catalog_v1_1_20261022/catalog.sqlite3", SOURCE["catalog_sha256"])
    return {
        "CHINATRAVEL_PROFILE": "expanded11",
        "CHINATRAVEL_DATABASE_ROOT": str(profile / DATABASE),
        "CT_SANDBOX_PROFILE_ROOT": str(profile),
        "CT_CATALOG_PATH": str(catalog),
        "CT_DYNAMIC_MODE": "snapshot",
        "CT_XIAMEN_HISTORICAL_FARES": "0",
        "CT_FLIGHT_REFERENCE_ROOT": str(installation / "observations/flight_20261116"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="Import the pinned upstream zip")
    setup.add_argument("archive", type=Path)
    setup_v20 = commands.add_parser("install-v20", help="Import the pinned twelve-city upstream zip")
    setup_v20.add_argument("archive", type=Path)
    commands.add_parser("info", help="Verify evidence and show selected paths")
    tool = commands.add_parser("tool", help="Run a read-only sandbox query")
    tool.add_argument("name", choices=(
        "catalog_list", "catalog_search", "catalog_entity", "catalog_preflight",
        "reference_search", "intercity_search", "goto",
    ))
    tool.add_argument("arguments", help="JSON object or path to a JSON file")
    run = commands.add_parser("run", help="Run an existing Python entry with this profile")
    run.add_argument("python_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command == "install":
        result = install(args.archive, args.data or DEFAULT_INSTALL)
    elif args.command == "install-v20":
        result = install_v20(args.archive, args.data or INSTALLS / V20_COMMIT[:7])
    else:
        env = configuration(args.data or default_installation())
        if args.command == "info":
            result = env
        elif args.command == "run":
            command = args.python_args
            if command[:1] == ["--"]:
                command = command[1:]
            if not command:
                parser.error("run expects Python arguments after --")
            return subprocess.call([sys.executable, "-X", "utf8", *command],
                                   cwd=PROJECT, env={**os.environ, **env})
        else:
            os.environ.update(env)
            from ..planning_access import _sandbox

            raw = args.arguments
            if not raw.lstrip().startswith("{"):
                raw = Path(raw).read_text(encoding="utf-8-sig")
            arguments = json.loads(raw)
            if not isinstance(arguments, dict):
                parser.error("tool arguments must be a JSON object")
            if env['CHINATRAVEL_PROFILE'] == 'expanded12' and args.name == 'intercity_search':
                # Match upstream's read-only benchmark query. The planner itself
                # keeps these historical fares outside the budget check.
                os.environ['CT_XIAMEN_HISTORICAL_FARES'] = '1'
            result = getattr(_sandbox(), args.name)(**arguments)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
