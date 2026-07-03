"""Audit log — the ``aihf_audit_events`` table from the v0.2 PRD (§九).

Every session start/end, tool call and promotion lands here. Backed by
SQLAlchemy Core (SQLite dev/tests · shared cloud Postgres prod); table name comes
from the ``aihf_`` registry in :mod:`polyagents.storage.tables`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, insert, select

from .engine import coerce_url, get_engine, make_engine
from .tables import audit_events, metadata


class AuditStore:
    def __init__(self, url_or_path: str | None = None, *, engine=None) -> None:
        if engine is not None:
            self.engine = engine
        elif url_or_path is None:
            self.engine = get_engine()
        else:
            self.engine = make_engine(coerce_url(url_or_path))
        metadata.create_all(self.engine, tables=[audit_events])

    def close(self) -> None:
        self.engine.dispose()

    def log(self, session_id: str, event_type: str, payload: dict | None = None,
            mode: str = "") -> None:
        with self.engine.begin() as cx:
            cx.execute(insert(audit_events).values(
                session_id=session_id, ts=datetime.now(timezone.utc).isoformat(),
                mode=mode, event_type=event_type,
                payload_json=json.dumps(payload or {}, ensure_ascii=False)))

    def recent(self, limit: int = 100, session_id: str | None = None) -> list[dict]:
        cols = ("session_id", "ts", "mode", "event_type")
        q = select(*[audit_events.c[c] for c in cols], audit_events.c.payload_json)
        if session_id:
            q = q.where(audit_events.c.session_id == session_id)
        q = q.order_by(audit_events.c.id.desc()).limit(int(limit))
        out = []
        with self.engine.connect() as cx:
            for r in cx.execute(q).fetchall():
                d = dict(zip(cols, r[:4]))
                try:
                    d["payload"] = json.loads(r[4] or "{}")
                except Exception:
                    d["payload"] = {}
                out.append(d)
        return out

    def count(self) -> int:
        with self.engine.connect() as cx:
            return cx.execute(select(func.count()).select_from(audit_events)).scalar_one()
