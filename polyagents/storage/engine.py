"""Database engine factory — one shared SQLAlchemy engine for the whole app.

The connection comes from ``POLYAGENTS_DATABASE_URL`` (or ``DATABASE_URL``); set
it to the cloud Postgres in prod so every web user reads/writes the same data.
With nothing set it falls back to a single local SQLite file (dev) — same code,
two backends. Credentials live in the env / .env, never in code.

    # prod (cloud, shared)
    POLYAGENTS_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/aihf
    # dev (default) → sqlite:///~/.polyagents/cache/aihf.db
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool


def database_url() -> str:
    """Resolve the DB URL: env first, else a local SQLite file (dev default)."""
    url = os.getenv("POLYAGENTS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if url:
        return url
    from polyagents.default_config import DEFAULT_CONFIG
    db = DEFAULT_CONFIG.get("db_path")
    path = Path(db).with_name("aihf.db") if db else Path.home() / ".polyagents" / "cache" / "aihf.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def make_engine(url: str) -> Engine:
    """Build an engine for ``url`` (handles the SQLite in-memory pooling quirk)."""
    if url in ("sqlite://", "sqlite:///:memory:"):          # shared in-memory (tests)
        return create_engine(url, future=True, poolclass=StaticPool,
                              connect_args={"check_same_thread": False})
    if url.startswith("sqlite"):
        return create_engine(url, future=True, connect_args={"check_same_thread": False})
    return create_engine(url, future=True, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide shared engine (prod Postgres or the dev SQLite default)."""
    return make_engine(database_url())


def coerce_url(url_or_path: str) -> str:
    """Map a convenience arg to a real URL: a URL stays; ``:memory:`` → in-memory
    SQLite; anything else is treated as a SQLite file path."""
    if "://" in url_or_path:
        return url_or_path
    if url_or_path == ":memory:":
        return "sqlite://"
    return f"sqlite:///{Path(url_or_path).as_posix()}"
