"""Parsing helpers shared across dataflows.

Lifted from the polymarket reference client — Gamma returns JSON-encoded
strings inside fields and mixed ISO date formats, both of which need defensive
parsing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def parse_json_field(value: Any) -> list:
    """Gamma encodes ``outcomes``/``clobTokenIds`` as JSON strings (or lists)."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp, tolerating a trailing ``Z`` and naive datetimes."""
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
