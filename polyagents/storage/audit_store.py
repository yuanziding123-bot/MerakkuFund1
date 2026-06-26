"""SQLite audit log — the ``audit_events`` table from the v0.2 PRD (§九).

Every session start/end and every tool call (and promotions) lands here, so a
mode-scoped :class:`~polyagents.runtime.session.AgentSession` has a recoverable
trail of what the agent did. Append-only; stdlib ``sqlite3`` only, same shape as
:class:`polyagents.storage.objects_store.ObjectStore`.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, ts TEXT, mode TEXT,
    event_type TEXT, payload_json TEXT
);
CREATE INDEX IF NOT EXISTS audit_session ON audit_events (session_id);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_events (ts);
"""


class AuditStore:
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

    def log(self, session_id: str, event_type: str, payload: dict | None = None,
            mode: str = "") -> None:
        self.conn.execute(
            "INSERT INTO audit_events (session_id, ts, mode, event_type, payload_json) "
            "VALUES (?,?,?,?,?)",
            (session_id, datetime.now(timezone.utc).isoformat(), mode, event_type,
             json.dumps(payload or {}, ensure_ascii=False)),
        )
        self.conn.commit()

    def recent(self, limit: int = 100, session_id: str | None = None) -> list[dict]:
        q = "SELECT session_id, ts, mode, event_type, payload_json FROM audit_events"
        args: list = []
        if session_id:
            q += " WHERE session_id=?"; args.append(session_id)
        q += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
        out = []
        for r in self.conn.execute(q, args).fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d.pop("payload_json") or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM audit_events").fetchone()["c"]
