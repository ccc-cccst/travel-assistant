"""Import completed runs and edits from the original local ShortTrip workspace."""

import argparse
import filecmp
import json
from pathlib import Path
import shutil
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT.parents[1] / ROOT.name / "result"
DESTINATION = ROOT / "result"
EXAMPLE = Path("experiments/P047_local_model_pilot/live_01/easy_61/framework")


def import_entry(source, target, result):
    if target.exists():
        if source.is_dir() and target.is_dir():
            for child in sorted(source.iterdir()):
                import_entry(child, target / child.name, result)
        elif not (source.is_file() and target.is_file() and filecmp.cmp(source, target, shallow=False)):
            result["conflicts"].append(target.relative_to(DESTINATION).as_posix())
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{uuid4().hex}.import")
    if source.is_dir():
        shutil.copytree(source, temporary)
    else:
        shutil.copy2(source, temporary)
    temporary.rename(target)
    result["copied"].append(target.relative_to(DESTINATION).as_posix())


def completed_job(folder):
    status = folder / "STATUS.json"
    if not status.is_file():
        return False
    value = json.loads(status.read_text(encoding="utf-8"))
    return value.get("protocol") == "integrated_planning_v1" and value.get("state") not in {"starting", "planning"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Choose a new report path.")
    result = {"copied": [], "conflicts": [], "jobs": []}
    for name in ("tripspec", "manual", "workbench_edits", "workbench_feedback"):
        source = ORIGINAL / name
        target = DESTINATION / name
        for path in sorted(source.iterdir()):
            if name == "manual" and not completed_job(path):
                continue
            unseen = not (target / path.name).exists()
            import_entry(path, target / path.name, result)
            if name == "manual" and unseen:
                result["jobs"].append(path.name)
    import_entry(ORIGINAL / EXAMPLE, DESTINATION / "workbench_examples" / "shanghai-qwen", result)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(f"Copied {len(result['copied'])} entries, imported {len(result['jobs'])} jobs, "
          f"preserved {len(result['conflicts'])} destination conflicts.")


if __name__ == "__main__":
    main()
