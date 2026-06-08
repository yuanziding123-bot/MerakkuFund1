"""Tests for MarketLens-inspired L2 microstructure features."""
from __future__ import annotations

from polyagents.dataflows.microstructure import compute_microstructure
from polyagents.dataflows.types import OrderBook, OrderBookLevel


def _book() -> OrderBook:
    return OrderBook(
        token_id="t",
        bids=[OrderBookLevel(0.44, 200.0), OrderBookLevel(0.43, 100.0)],
        asks=[OrderBookLevel(0.46, 80.0), OrderBookLevel(0.47, 120.0)],
    )


def test_touch_and_spread():
    d = compute_microstructure(_book())
    assert d["best_bid"] == 0.44
    assert d["best_ask"] == 0.46
    assert d["mid"] == 0.45
    assert round(d["spread_bps"], 1) == 444.4
    assert d["queue_ahead_bid"] == 200.0
    assert d["queue_ahead_ask"] == 80.0


def test_micro_price_leans_to_thin_side():
    d = compute_microstructure(_book())
    # ask side is thinner (80 vs 200) -> micro price pulled toward the ask.
    assert d["mid"] < d["micro_price"] < d["best_ask"]
    assert round(d["micro_price"], 4) == round((0.44 * 80 + 0.46 * 200) / 280, 4)


def test_depth_imbalance_and_pressure():
    d = compute_microstructure(_book())
    assert round(d["imbalance_L1"], 4) == round(120 / 280, 4)   # (200-80)/280
    # L5 sees all levels: bid 300 vs ask 200 -> +0.2
    assert round(d["imbalance_L5"], 4) == 0.2
    assert d["book_pressure"] == d["imbalance_L5"]


def test_empty_book_via_none(fake_client):
    # sanity: a populated fake book is "available"
    d = compute_microstructure(fake_client.fetch_order_book("t"))
    assert d["available"] is True
