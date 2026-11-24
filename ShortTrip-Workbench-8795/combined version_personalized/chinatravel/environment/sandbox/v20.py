"""Install and verify the upstream twelve-city snapshot for local planning."""

from functools import lru_cache
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from zipfile import ZipFile

from .install import checked_file


COMMIT = "6de12f41ce0e523afade7162807a5df3ea82b2bc"
PROFILE = "xiamen12_historical_v20_completion"
RECEIPT_SHA256 = "e5bae46e2dc9cbcd77f66138463ace7628e27dde6a4c568aaeb05106adcd3106"
ARCHIVE_PREFIX = f"chinatravel-five-city-{COMMIT}/data/"


@lru_cache(maxsize=1)
def verify(profile):
    receipt = checked_file(profile, "receipt.json", RECEIPT_SHA256)
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    if manifest.get("schema") != "xiamen12-historical-v1":
        raise ValueError("Unexpected twelve-city receipt schema")
    declared = manifest["files"]
    for relative, expected in declared.items():
        checked_file(profile, relative, expected)
    actual = {
        path.relative_to(profile).as_posix() for path in profile.rglob("*")
        if path.is_file() and path != receipt and "__pycache__" not in path.parts
    }
    if actual != declared.keys():
        raise ValueError("Twelve-city profile file inventory changed")
    return len(declared)


def install_v20(archive, output):
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    profile_prefix = f"sandbox_profiles/{PROFILE}/"
    with tempfile.TemporaryDirectory(prefix="shorttrip-v20-", dir=output.parent) as temp:
        stage = Path(temp) / "installation"
        with ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or not member.filename.startswith(ARCHIVE_PREFIX):
                    continue
                relative = member.filename.removeprefix(ARCHIVE_PREFIX)
                if not relative.startswith((profile_prefix, "query_sets/xian11/")):
                    continue
                parts = PurePosixPath(relative).parts
                target = stage.joinpath(*parts).resolve()
                if not target.is_relative_to(stage.resolve()):
                    raise ValueError(f"Unsafe archive entry: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
        files = verify(stage / profile_prefix)
        receipt = {
            "repository": "ruojimy/chinatravel-five-city",
            "commit": COMMIT,
            "profile": PROFILE,
            "public_receipt_sha256": RECEIPT_SHA256,
            "verified_profile_files": files,
        }
        (stage / "install.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)
    return receipt
