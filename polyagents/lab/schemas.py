"""Schemas for the AIHF Lab module.

These dataclasses mirror the public JSON contract in
``docs/product/lab-module-api-data-contract.md`` while keeping the first Lab
implementation lightweight and dependency-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


HypothesisState = Literal["draft", "lab", "paper", "live", "archived"]
BacktestStatus = Literal["queued", "running", "completed", "failed"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, kw_only=True)
class CreateHypothesisRequest:
    statement: str
    category_filter: str
    feature_set: list[str] = field(default_factory=list)
    prompt_version: str = "signal-v1"
    model_version: str = "unknown"
    lineage: dict[str, Any] = field(default_factory=lambda: {"source": "manual", "parents": []})

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("statement is required")
        if not self.category_filter.strip():
            raise ValueError("category_filter is required")


@dataclass(frozen=True, kw_only=True)
class CreateHypothesisResponse:
    id: str
    state: HypothesisState
    version: int
    snapshot_id: str


@dataclass(frozen=True, kw_only=True)
class HypothesisRecord:
    id: str
    type: Literal["hypothesis"] = "hypothesis"
    version: int = 1
    state: HypothesisState = "draft"
    owner: str = "default"
    statement: str
    category_filter: str
    feature_set: list[str] = field(default_factory=list)
    prompt_version: str = "signal-v1"
    model_version: str = "unknown"
    snapshot_id: str
    lineage: dict[str, Any] = field(default_factory=dict)
    eval_summary: dict[str, Any] | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, kw_only=True)
class BacktestRequest:
    hypothesis_id: str
    time_window: dict[str, str]
    market_filter: dict[str, Any]
    model_version: str
    prompt_version: str
    calibrator_id: str
    pit_strict: bool = True
    max_markets: int = 100

    def __post_init__(self) -> None:
        start = self.time_window.get("start")
        end = self.time_window.get("end")
        if not start or not end:
            raise ValueError("time_window.start and time_window.end are required")
        if _parse_time(start) >= _parse_time(end):
            raise ValueError("time_window.start must be before end")
        if self.market_filter.get("settled_only") is not True:
            raise ValueError("Lab MVP backtests require settled_only=true")
        if not 1 <= self.max_markets <= 500:
            raise ValueError("max_markets must be between 1 and 500")


@dataclass(frozen=True, kw_only=True)
class ForecastRecord:
    id: str
    hypothesis_id: str
    market_token_id: str
    snapshot_id: str
    p_raw: float
    p_cal: float
    p_market: float
    outcome: int | None
    model_version: str
    prompt_version: str
    calibrator_id: str
    prediction_time: str
    available_at_max: str | None = None


@dataclass(frozen=True, kw_only=True)
class BacktestRunResult:
    id: str
    hypothesis_id: str
    status: BacktestStatus
    report_id: str | None = None
    forecast_count: int = 0
    error: str | None = None
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
