"""Domain types for the data-collection layer.

Adapted from the polymarket reference implementation's ``src/data/types.py``,
trimmed to what the data layer produces. Trading/position types belong to the
later execution layer and are intentionally omitted here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Outcome = Literal["YES", "NO"]


@dataclass
class Market:
    """One tradeable side (YES or NO) of a Polymarket market."""

    market_id: str
    condition_id: str
    question: str
    description: str
    outcome: Outcome
    token_id: str
    price: float
    volume_24h: float
    liquidity: float
    spread: float
    days_to_expiry: float
    expiry: datetime
    raw: dict = field(default_factory=dict)


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    token_id: str
    bids: list[OrderBookLevel]   # sorted best (highest) bid first
    asks: list[OrderBookLevel]   # sorted best (lowest) ask first

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid
