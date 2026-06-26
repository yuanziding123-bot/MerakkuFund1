"""SQLite persistence for the 5 financial objects + their promotion events.

Per the v0.2 design (§九) objects live in ONE table with a JSON payload — not the
17 tables of v0.1. Common fields are columns (so we can query by type/state);
everything type-specific rides in ``payload_json``. Promotions are appended to a
separate audit table so every state change is recoverable.

Stdlib ``sqlite3`` only, same shape as :class:`polyagents.storage.db.DataStore`.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from polyagents.objects import FO, from_dict, promote, to_dict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id TEXT PRIMARY KEY,
    type TEXT, version INTEGER, state TEXT, owner TEXT,
    snapshot_id TEXT, created_at TEXT, updated_at TEXT,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS objects_type_state ON objects (type, state);
CREATE TABLE IF NOT EXISTS promotion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT, from_state TEXT, to_state TEXT,
    promoted_by TEXT, evidence_ref TEXT, promoted_at TEXT
);
"""


class ObjectStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ----- CRUD --------------------------------------------------------------

    def save(self, fo: FO) -> FO:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?,?,?)",
            (fo.id, fo.type, fo.version, fo.state, fo.owner, fo.snapshot_id,
             fo.created_at, now, json.dumps(to_dict(fo), ensure_ascii=False)),
        )
        self.conn.commit()
        return fo

    def get(self, object_id: str) -> FO | None:
        r = self.conn.execute("SELECT payload_json FROM objects WHERE id=?",
                              (object_id,)).fetchone()
        return from_dict(json.loads(r["payload_json"])) if r else None

    def list(self, type: str | None = None, state: str | None = None) -> list[FO]:
        q = "SELECT payload_json FROM objects"
        clauses, args = [], []
        if type:
            clauses.append("type=?"); args.append(type)
        if state:
            clauses.append("state=?"); args.append(state)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY updated_at DESC"
        return [from_dict(json.loads(r["payload_json"]))
                for r in self.conn.execute(q, args).fetchall()]

    # ----- promotion (state change + audit) ----------------------------------

    def promote(self, object_id: str, to_state: str, *, promoted_by: str,
                evidence_ref: str | None = None) -> FO:
        """Load, advance one legal edge, persist the new version, and audit it."""
        fo = self.get(object_id)
        if fo is None:
            raise KeyError(object_id)
        from_state = fo.state
        moved = promote(fo, to_state, promoted_by=promoted_by, evidence_ref=evidence_ref)
        self.save(moved)
        self.conn.execute(
            "INSERT INTO promotion_events "
            "(object_id, from_state, to_state, promoted_by, evidence_ref, promoted_at) "
            "VALUES (?,?,?,?,?,?)",
            (object_id, from_state, to_state, promoted_by, evidence_ref,
             moved.lineage.events[-1].promoted_at),
        )
        self.conn.commit()
        return moved

    def promotions(self, object_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT from_state, to_state, promoted_by, evidence_ref, promoted_at "
            "FROM promotion_events WHERE object_id=? ORDER BY id", (object_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT type, COUNT(*) c FROM objects GROUP BY type").fetchall()
        return {r["type"]: r["c"] for r in rows}
