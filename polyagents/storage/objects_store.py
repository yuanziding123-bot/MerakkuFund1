"""Persistence for the 5 financial objects + their promotion events.

Per the v0.2 design (§九) objects live in ONE table with a JSON payload — not the
17 tables of v0.1. Backed by SQLAlchemy Core so the same code runs on SQLite
(dev/tests) and the shared cloud PostgreSQL (prod). Table names come from the
``aihf_`` registry in :mod:`polyagents.storage.tables` — never hard-coded here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, insert, select

from polyagents.objects import FO, from_dict, promote, to_dict

from .engine import coerce_url, get_engine, make_engine
from .tables import metadata, objects, promotion_events


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObjectStore:
    def __init__(self, url_or_path: str | None = None, *, engine=None) -> None:
        # url_or_path: None → shared app engine (prod Postgres / dev SQLite);
        # ":memory:" / a file path / a URL → a fresh engine (tests / explicit).
        if engine is not None:
            self.engine = engine
        elif url_or_path is None:
            self.engine = get_engine()
        else:
            self.engine = make_engine(coerce_url(url_or_path))
        metadata.create_all(self.engine, tables=[objects, promotion_events])

    def close(self) -> None:
        self.engine.dispose()

    # ----- CRUD --------------------------------------------------------------

    def save(self, fo: FO) -> FO:
        row = {
            "id": fo.id, "type": fo.type, "version": fo.version, "state": fo.state,
            "owner": fo.owner, "snapshot_id": fo.snapshot_id, "created_at": fo.created_at,
            "updated_at": _now(), "payload_json": json.dumps(to_dict(fo), ensure_ascii=False),
        }
        with self.engine.begin() as cx:                  # upsert by id: delete then insert (portable)
            cx.execute(delete(objects).where(objects.c.id == fo.id))
            cx.execute(insert(objects).values(**row))
        return fo

    def get(self, object_id: str) -> FO | None:
        with self.engine.connect() as cx:
            r = cx.execute(
                select(objects.c.payload_json).where(objects.c.id == object_id)
            ).fetchone()
        return from_dict(json.loads(r[0])) if r else None

    def list(self, type: str | None = None, state: str | None = None) -> list[FO]:
        q = select(objects.c.payload_json)
        if type:
            q = q.where(objects.c.type == type)
        if state:
            q = q.where(objects.c.state == state)
        q = q.order_by(objects.c.updated_at.desc())
        with self.engine.connect() as cx:
            rows = cx.execute(q).fetchall()
        return [from_dict(json.loads(r[0])) for r in rows]

    # ----- promotion (state change + audit) ----------------------------------

    def promote(self, object_id: str, to_state: str, *, promoted_by: str,
                evidence_ref: str | None = None) -> FO:
        fo = self.get(object_id)
        if fo is None:
            raise KeyError(object_id)
        from_state = fo.state
        moved = promote(fo, to_state, promoted_by=promoted_by, evidence_ref=evidence_ref)
        self.save(moved)
        with self.engine.begin() as cx:
            cx.execute(insert(promotion_events).values(
                object_id=object_id, from_state=from_state, to_state=to_state,
                promoted_by=promoted_by, evidence_ref=evidence_ref,
                promoted_at=moved.lineage.events[-1].promoted_at))
        return moved

    def promotions(self, object_id: str) -> list[dict]:
        cols = ("from_state", "to_state", "promoted_by", "evidence_ref", "promoted_at")
        q = (select(*[promotion_events.c[c] for c in cols])
             .where(promotion_events.c.object_id == object_id)
             .order_by(promotion_events.c.id))
        with self.engine.connect() as cx:
            return [dict(zip(cols, r)) for r in cx.execute(q).fetchall()]

    def counts(self) -> dict[str, int]:
        q = select(objects.c.type, func.count()).group_by(objects.c.type)
        with self.engine.connect() as cx:
            return {t: n for t, n in cx.execute(q).fetchall()}
