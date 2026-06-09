"""MarketLens adapter — tick-level L2 order book history (Merakku v3.0 Layer 1 P0).

MarketLens (marketlens.trade) is a commercial Polymarket order-book history
service: tick-level L2 snapshots/deltas, replay, and backtesting. The ``pip
install marketlens`` SDK is free, but **data access needs a paid API key**
(``MARKETLENS_API_KEY``). This adapter wraps it so the rest of polyagents speaks
its own ``OrderBook`` type and degrades gracefully when no key is set.

Unlike the live ``PolymarketDataClient`` order book (free, current snapshot),
MarketLens gives *historical* L2 — including resolved markets — which is what
the doc's Phase 1 task ("pull 3-5 resolved markets' L2") and backtesting need.
The same :func:`compute_microstructure` runs on these books too.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from .types import OrderBook, OrderBookLevel


@dataclass
class ResolvedL2:
    """One resolved market's identity + its L2 book at a point in time."""

    market_id: str
    question: str
    platform: str
    status: str
    winning_outcome: Optional[str]
    as_of: Optional[str]
    book: OrderBook


def _levels(raw_levels, descending: bool) -> list[OrderBookLevel]:
    out: list[OrderBookLevel] = []
    for lvl in raw_levels or []:
        try:
            out.append(OrderBookLevel(price=float(lvl.price), size=float(lvl.size)))
        except (AttributeError, TypeError, ValueError):
            continue
    out.sort(key=lambda l: l.price, reverse=descending)
    return out


def to_order_book(ml_book: Any) -> OrderBook | None:
    """Map a marketlens ``OrderBook`` into polyagents' ``OrderBook`` type."""
    if ml_book is None:
        return None
    bids = _levels(getattr(ml_book, "bids", None), descending=True)
    asks = _levels(getattr(ml_book, "asks", None), descending=False)
    if not bids and not asks:
        return None
    return OrderBook(token_id=str(getattr(ml_book, "market_id", "")), bids=bids, asks=asks)


class MarketLensClient:
    """Read-only MarketLens wrapper. ``enabled`` is False without an API key."""

    def __init__(self, api_key: str | None = None, client: Any | None = None) -> None:
        self.api_key = api_key or os.getenv("MARKETLENS_API_KEY")
        self._client = client          # injectable for tests
        self._import_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self):
        if self._client is not None:
            return self._client
        try:
            from marketlens import MarketLens
        except ImportError as exc:
            self._import_error = f"marketlens not installed: {exc}"
            return None
        self._client = MarketLens(api_key=self.api_key)
        return self._client

    def list_resolved_markets(self, limit: int = 5, platform: str = "polymarket") -> list[Any]:
        """Up to ``limit`` resolved markets (filtered client-side to be safe)."""
        client = self._get()
        if client is None:
            return []
        out: list[Any] = []
        try:
            for m in client.markets.list(platform=platform, status="resolved"):
                if str(getattr(m, "status", "")).lower().endswith("resolved"):
                    out.append(m)
                if len(out) >= limit:
                    break
        except Exception:
            return out
        return out

    def get_l2(self, market_id: str, at: Any | None = None, depth: int | None = None) -> OrderBook | None:
        """L2 book for a market, optionally at a historical timestamp ``at``."""
        client = self._get()
        if client is None:
            return None
        try:
            ml_book = client.orderbook.get(market_id, at=at, depth=depth)
        except Exception:
            return None
        return to_order_book(ml_book)

    def sample_resolved_l2(self, n: int = 3, depth: int | None = 10, store=None) -> list[ResolvedL2]:
        """Pull L2 books for ``n`` resolved markets (doc Phase 1 W1 sample set).

        Uses each market's last book before resolution. Persists each book to the
        ``orderbook_snapshots`` table when a ``store`` is given.
        """
        out: list[ResolvedL2] = []
        for m in self.list_resolved_markets(limit=n):
            at = getattr(m, "platform_resolved_at", None) or getattr(m, "resolved_at", None)
            book = self.get_l2(str(m.id), at=at, depth=depth)
            if book is None:
                continue
            sample = ResolvedL2(
                market_id=str(m.id),
                question=str(getattr(m, "question", "")),
                platform=str(getattr(m, "platform", "")),
                status=str(getattr(m, "status", "")),
                winning_outcome=getattr(m, "winning_outcome", None),
                as_of=str(at) if at else None,
                book=book,
            )
            out.append(sample)
            if store is not None:
                from .microstructure import compute_microstructure

                store.record_orderbook(sample.market_id, compute_microstructure(book), ts=sample.as_of)
        return out
