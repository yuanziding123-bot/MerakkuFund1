"""Table registry — the ONE place table names are defined.

Per the team's DB convention: every table is prefixed ``aihf_`` for project
isolation, and code references these Table objects / the name helpers here —
**never hard-coded name strings**. Configurable via env:

* ``AIHF_TABLE_PREFIX``  (default ``aihf_``)
* ``AIHF_DB_SCHEMA``     (Postgres schema for isolation; unset/None on SQLite)

Defined with SQLAlchemy Core so the same definitions run on SQLite (dev/tests)
and PostgreSQL (prod) — integer PKs become AUTOINCREMENT or IDENTITY per dialect.
"""
from __future__ import annotations

import os

from sqlalchemy import Column, Index, Integer, MetaData, Table, Text

TABLE_PREFIX = os.getenv("AIHF_TABLE_PREFIX", "aihf_")
DB_SCHEMA = os.getenv("AIHF_DB_SCHEMA") or None      # None → default schema (works on SQLite)


def table_name(base: str) -> str:
    """The configured physical table name for a logical base name."""
    return f"{TABLE_PREFIX}{base}"


metadata = MetaData(schema=DB_SCHEMA)

# ----- objects + promotions (was objects_store.py's raw schema) --------------

objects = Table(
    table_name("objects"), metadata,
    Column("id", Text, primary_key=True),
    Column("type", Text), Column("version", Integer), Column("state", Text),
    Column("owner", Text), Column("snapshot_id", Text),
    Column("created_at", Text), Column("updated_at", Text),
    Column("payload_json", Text),
    Index(f"ix_{table_name('objects')}_type_state", "type", "state"),
)

promotion_events = Table(
    table_name("promotion_events"), metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("object_id", Text), Column("from_state", Text), Column("to_state", Text),
    Column("promoted_by", Text), Column("evidence_ref", Text), Column("promoted_at", Text),
    Index(f"ix_{table_name('promotion_events')}_object", "object_id"),
)

# ----- audit (was audit_store.py's raw schema) -------------------------------

audit_events = Table(
    table_name("audit_events"), metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Text), Column("ts", Text), Column("mode", Text),
    Column("event_type", Text), Column("payload_json", Text),
    Index(f"ix_{table_name('audit_events')}_session", "session_id"),
    Index(f"ix_{table_name('audit_events')}_ts", "ts"),
)
