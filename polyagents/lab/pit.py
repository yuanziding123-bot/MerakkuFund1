"""Point-in-time invariants for Lab backtests."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assert_point_in_time(
    feature_rows: Iterable[dict],
    prediction_time: str | datetime,
    *,
    strict: bool = True,
) -> None:
    """Reject features that were unavailable at prediction time.

    In strict mode, missing ``available_at`` is also a failure because it cannot
    be audited for leakage.
    """
    pred = _parse_time(prediction_time)
    for row in feature_rows:
        available_at = row.get("available_at")
        if available_at is None:
            if strict:
                raise ValueError(f"PIT violation: missing available_at for {row.get('feature', 'feature')}")
            continue
        available = _parse_time(available_at)
        if available > pred:
            raise ValueError(
                "PIT violation: feature available_at is after prediction_time "
                f"({available.isoformat()} > {pred.isoformat()})"
            )
