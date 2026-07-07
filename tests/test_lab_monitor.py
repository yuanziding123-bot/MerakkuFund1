"""Tests for Lab dry-run opportunity monitor."""
from __future__ import annotations

from datetime import datetime, timezone

from polyagents.dataflows.types import Candle, Market, OrderBook, OrderBookLevel
from polyagents.lab.monitor import LabMonitor, MonitorRequest


class _FakeClient:
    submit_order_called = False

    def __init__(self) -> None:
        self.market = Market(
            market_id="m1",
            condition_id="cond1",
            question="Will BTC close above 100k?",
            description="",
            outcome="YES",
            token_id="yes-token",
            price=0.50,
            volume_24h=100000.0,
            liquidity=25000.0,
            spread=0.01,
            days_to_expiry=10.0,
            expiry=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

    def list_active_markets(self, limit: int = 20) -> list[dict]:
        return [{"id": "m1"}]

    def to_markets(self, raw_markets):
        return [self.market]

    def fetch_price_history(self, token_id: str, interval: str = "1w", fidelity: int = 60):
        return [
            Candle(ts=datetime(2026, 7, 1, tzinfo=timezone.utc), open=0.20, high=0.20, low=0.20, close=0.20, volume=0.0),
            Candle(ts=datetime(2026, 7, 2, tzinfo=timezone.utc), open=0.50, high=0.50, low=0.50, close=0.50, volume=0.0),
        ]

    def fetch_order_book(self, token_id: str):
        return OrderBook(
            token_id=token_id,
            bids=[OrderBookLevel(price=0.495, size=1000.0)],
            asks=[OrderBookLevel(price=0.505, size=900.0)],
        )

    def fetch_market_trades(self, condition_id: str, min_ts=None, max_pages: int = 25):
        return [
            {"asset": "yes-token", "timestamp": 1783000000, "size": "100", "price": "0.50", "side": "BUY"},
            {"asset": "yes-token", "timestamp": 1783000001, "size": "25", "price": "0.49", "side": "SELL"},
        ]

    def submit_order(self, *args, **kwargs):
        self.submit_order_called = True
        raise AssertionError("monitor must not execute orders")


def test_lab_monitor_returns_dry_run_opportunity():
    client = _FakeClient()
    monitor = LabMonitor(client=client)

    result = monitor.scan(MonitorRequest(strategy_id="momentum-v1", limit=5, include_holds=True))

    assert result["dry_run"] is True
    assert result["n"] == 1
    opp = result["opportunities"][0]
    assert opp["dry_run"] is True
    assert opp["market_token_id"] == "yes-token"
    assert opp["strategy_id"] == "momentum-v1"
    assert opp["p_raw"] > opp["market_price"]
    assert opp["edge"] > 0
    assert opp["action"] in {"buy", "hold"}
    assert "price_momentum" in opp["signal_model"]["feature_vector"]
    assert client.submit_order_called is False


def test_lab_monitor_can_filter_holds():
    client = _FakeClient()
    client.market.price = 0.90
    monitor = LabMonitor(client=client)

    result = monitor.scan(MonitorRequest(strategy_id="market-naive-v1", limit=5, include_holds=False))

    assert result["dry_run"] is True
    assert result["opportunities"] == []
    assert result["message"] == "no opportunity"
