"""Install the pinned evidence profile and ten-city catalog from a GitHub zip."""

from contextlib import closing
from hashlib import file_digest
from pathlib import Path, PurePosixPath
import gzip
import json
import shutil
import sqlite3
import tempfile
import zipfile


PROJECT = Path(__file__).resolve().parents[3]
SOURCE = json.loads(Path(__file__).with_name("sources.json").read_text(encoding="utf-8"))["five_city"]
DEFAULT_INSTALL = PROJECT / "artifacts" / "data" / SOURCE["commit"][:7]
DATABASE = Path("expanded/G3_ext11/chinatravel/environment/database")
SNAPSHOT = Path("data_integration/dynamic_snapshot_v2.json")


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def checked_file(root, relative, expected):
    relative = Path(relative)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or unsafe profile file: {relative}")
    if digest(path) != expected:
        raise ValueError(f"Profile hash mismatch: {relative}")
    return path


def verify_profile(profile):
    profile = profile.resolve()
    snapshot = checked_file(profile, SNAPSHOT, SOURCE["snapshot_sha256"])
    data = json.loads(snapshot.read_text(encoding="utf-8-sig"))
    if data["schema"] != "chinatravel-dynamic-snapshot-v4.2":
        raise ValueError("Unsupported snapshot schema")
    inventory = profile / "profile_file_hashes.json"
    receipt = json.loads((profile / "profile_install_receipt.json").read_text(encoding="utf-8-sig"))
    if receipt["inventory_sha256"] != SOURCE["inventory_sha256"]:
        raise ValueError("Profile inventory receipt does not match the pinned source")
    checked_file(profile, inventory.name, SOURCE["inventory_sha256"])
    declared = json.loads(inventory.read_text(encoding="utf-8-sig"))
    for relative, expected in declared.items():
        checked_file(profile, relative, expected)
    for entry in data["evidence_files"]:
        checked_file(snapshot.parent, entry["path"], entry["sha256"])
    for entry in data["changed_direct_files"]:
        checked_file(profile, entry["path"], entry["after_sha256"])
    actual = {
        path.relative_to(profile).as_posix() for path in profile.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.name not in {"profile_file_hashes.json", "profile_install_receipt.json"}
    }
    if actual != declared.keys():
        raise ValueError("Profile file inventory changed")
    return len(declared)


def install(archive, output):
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    root = f"chinatravel-five-city-{SOURCE['commit']}/data/"
    profile_prefix = f"sandbox_profiles/{SOURCE['profile']}/"
    catalog_prefix = "catalog_v1_1_20261022/"
    prefixes = (profile_prefix, catalog_prefix, "observations/", "query_sets/xian11/")
    with tempfile.TemporaryDirectory(prefix="shorttrip-data-", dir=output.parent) as temporary:
        stage = Path(temporary) / "installed"
        stage.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or not member.filename.startswith(root):
                    continue
                relative = member.filename.removeprefix(root)
                if not relative.startswith(prefixes):
                    continue
                parts = PurePosixPath(relative).parts
                target = stage.joinpath(*parts).resolve()
                if not target.is_relative_to(stage.resolve()):
                    raise ValueError(f"Unsafe archive entry: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)

        files = verify_profile(stage / profile_prefix)
        catalog = stage / catalog_prefix / "catalog.sqlite3"
        compressed = checked_file(stage, catalog_prefix + "catalog.sqlite3.gz", SOURCE["catalog_gzip_sha256"])
        with gzip.open(compressed, "rb") as source, catalog.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        checked_file(stage, catalog.relative_to(stage), SOURCE["catalog_sha256"])
        with closing(sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True)) as connection:
            counts = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in ("records", "tags", "claims")}
        receipt = {"source": SOURCE, "profile_files": files, "catalog_counts": counts}
        (stage / "install.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)
    return receipt
