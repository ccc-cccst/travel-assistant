"""Persist local itineraries with atomic, version-checked writes."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .edits import EditConflict


class SavedTrips:
    def __init__(self):
        default = Path(__file__).resolve().parents[2] / "artifacts" / "local" / "shorttrip-itineraries.sqlite3"
        self.path = Path(os.getenv("SHORTTRIP_ITINERARY_STORE") or default)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS itineraries
                       (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, updated TEXT NOT NULL, content TEXT NOT NULL)""")

    def get(self, key):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT revision, updated, content FROM itineraries WHERE id = ?", (key,)).fetchone()
        if row is None:
            raise EditConflict("missing_trip", "找不到这份本机行程。")
        revision, updated, content = row
        return {**json.loads(content), "id": key, "revision": revision, "updated": updated}

    def list(self):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT id, revision, updated, content FROM itineraries ORDER BY updated DESC").fetchall()
        return [{"id": key, "revision": revision, "updated": updated, "title": json.loads(content)["title"]}
                for key, revision, updated, content in rows]

    def save(self, document):
        key, revision = document.get("id"), document.get("revision", 0)
        updated = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        content = {k: v for k, v in document.items() if k not in {"id", "revision", "updated"}}
        packed = json.dumps(content, ensure_ascii=False, allow_nan=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            if key is None:
                key = uuid4().hex
                db.execute("INSERT INTO itineraries VALUES (?, ?, ?, ?)", (key, 1, updated, packed))
            else:
                cursor = db.execute(
                    "UPDATE itineraries SET revision = ?, updated = ?, content = ? WHERE id = ? AND revision = ?",
                    (revision + 1, updated, packed, key, revision),
                )
                if cursor.rowcount != 1:
                    raise EditConflict("stale_revision", "行程已在其他窗口更新，请重新打开后再编辑。")
        return {**content, "id": key, "revision": revision + 1, "updated": updated}

