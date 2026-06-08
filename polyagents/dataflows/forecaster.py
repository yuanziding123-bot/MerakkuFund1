"""Candle forecasting hook — inspired by Kronos.

Kronos is a Transformer foundation model for OHLCV candles. It's a P3 "watch"
item: too heavy to ship in the data layer today. We define the integration
seam now — a ``CandleForecaster`` protocol — so a Kronos (or any) model can be
dropped in later without changing the graph. The default ``NullForecaster``
returns ``None`` (no forecast), and the feature extractor simply omits forecast
factors when that happens.

A forecast, when produced, is a dict the feature layer can flatten, e.g.
``{"direction": 1, "expected_move": 0.03, "confidence": 0.6}``.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class CandleForecaster(Protocol):
    def forecast(self, closes: list[float]) -> Optional[dict[str, Any]]:
        """Predict from a close-price series, or return None if not applicable."""
        ...


class NullForecaster:
    """Default no-op forecaster — the Kronos seam with nothing plugged in."""

    def forecast(self, closes: list[float]) -> Optional[dict[str, Any]]:
        return None
