"""Lab subsystem: hypothesis research, replay, and evidence persistence."""
from __future__ import annotations

from .backtest import BacktestRunner, PointInTimeError, momentum_signal, naive_signal
from .monitor import LabMonitor, MonitorOpportunity, MonitorRequest
from .schemas import (
    BacktestRequest,
    BacktestRunResult,
    CreateHypothesisRequest,
    CreateHypothesisResponse,
    ForecastRecord,
    HypothesisRecord,
)
from .strategies import DEFAULT_STRATEGY_ID, STRATEGIES, LabStrategy, get_strategy

__all__ = [
    "BacktestRequest",
    "BacktestRunResult",
    "BacktestRunner",
    "CreateHypothesisRequest",
    "CreateHypothesisResponse",
    "ForecastRecord",
    "HypothesisRecord",
    "LabMonitor",
    "MonitorOpportunity",
    "MonitorRequest",
    "DEFAULT_STRATEGY_ID",
    "PointInTimeError",
    "STRATEGIES",
    "LabStrategy",
    "get_strategy",
    "momentum_signal",
    "naive_signal",
]
