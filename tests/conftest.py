"""Shared fakes for the data-collection tests.

The whole layer is built around clients being injectable, so tests substitute a
``FakeClient`` and never touch the network — the same approach used by both
polymarket and TradingAgents.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polyagents.dataflows.types import Candle, Market, OrderBook, OrderBookLevel

TOKEN = "tok_yes_0001"
CONDITION = "0xcondition"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class FakeClient:
    """In-memory stand-in for PolymarketDataClient with deterministic data."""

    def __init__(self) -> None:
        now = _now()
        # 4 hourly candles, gently rising price.
        self._candles = [
            Candle(ts=now - timedelta(hours=3 - i), open=0.40 + i * 0.02, high=0.0, low=0.0,
                   close=0.40 + i * 0.02, volume=0.0)
            for i in range(4)
        ]
        # Trades land inside the candle window; two buys, one sell.
        self._trades = [
            {"asset": TOKEN, "timestamp": int((now - timedelta(hours=2)).timestamp()),
             "size": 100.0, "price": 0.42, "side": "BUY"},
            {"asset": TOKEN, "timestamp": int((now - timedelta(hours=1)).timestamp()),
             "size": 50.0, "price": 0.44, "side": "BUY"},
            {"asset": TOKEN, "timestamp": int((now - timedelta(minutes=30)).timestamp()),
             "size": 30.0, "price": 0.45, "side": "SELL"},
            # A different-asset trade that must be ignored by the token filter.
            {"asset": "tok_no_9999", "timestamp": int(now.timestamp()),
             "size": 999.0, "price": 0.55, "side": "BUY"},
        ]

    def fetch_price_history(self, token_id, interval="1h", fidelity=60):
        return list(self._candles)

    def fetch_market_trades(self, condition_id, min_ts=None, max_pages=25):
        if min_ts is None:
            return list(self._trades)
        return [t for t in self._trades if t["timestamp"] >= min_ts]

    def fetch_order_book(self, token_id):
        return OrderBook(
            token_id=token_id,
            bids=[OrderBookLevel(0.44, 200.0), OrderBookLevel(0.43, 100.0)],
            asks=[OrderBookLevel(0.46, 80.0), OrderBookLevel(0.47, 120.0)],
        )


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def sample_market() -> Market:
    return Market(
        market_id="mkt1",
        condition_id=CONDITION,
        question="Will it rain tomorrow?",
        description="desc",
        outcome="YES",
        token_id=TOKEN,
        price=0.45,
        volume_24h=125000.0,
        liquidity=50000.0,
        spread=0.02,
        days_to_expiry=3.0,
        expiry=_now() + timedelta(days=3),
    )
