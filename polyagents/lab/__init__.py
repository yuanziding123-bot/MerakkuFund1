"""Lab subsystem: hypothesis research, replay, and evidence persistence."""
from __future__ import annotations

from .backtest import BacktestRunner, PointInTimeError, momentum_signal, naive_signal
from .schemas import (
    BacktestRequest,
    BacktestRunResult,
    CreateHypothesisRequest,
    CreateHypothesisResponse,
    ForecastRecord,
    HypothesisRecord,
)

__all__ = [
    "BacktestRequest",
    "BacktestRunResult",
    "BacktestRunner",
    "CreateHypothesisRequest",
    "CreateHypothesisResponse",
    "ForecastRecord",
    "HypothesisRecord",
    "PointInTimeError",
    "momentum_signal",
    "naive_signal",
]
