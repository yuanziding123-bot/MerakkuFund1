"""Tests for the MarketLens adapter (mapping + sampling, with fakes — no key/net)."""
from __future__ import annotations

from polyagents.dataflows.marketlens_client import MarketLensClient, to_order_book
from polyagents.dataflows.microstructure import compute_microstructure


# --- fakes mimicking the marketlens SDK objects ------------------------------

class _Lvl:
    def __init__(self, price, size):
        self.price = price
        self.size = size


class _MLBook:
    def __init__(self, market_id, bids, asks):
        self.market_id = market_id
        self.bids = [_Lvl(p, s) for p, s in bids]
        self.asks = [_Lvl(p, s) for p, s in asks]


class _Market:
    def __init__(self, id, status="resolved"):
        self.id = id
        self.status = status
        self.question = f"Q {id}?"
        self.platform = "polymarket"
        self.winning_outcome = "Yes"
        self.platform_resolved_at = "2026-04-15T02:00:00Z"


class _FakeML:
    """Mimics the marketlens client surface used by the adapter."""

    class _Markets:
        def __init__(self, markets):
            self._m = markets

        def list(self, **params):
            return iter(self._m)

    class _OrderBook:
        def __init__(self, books):
            self._b = books

        def get(self, market_id, at=None, depth=None):
            return self._b[market_id]

    def __init__(self, markets, books):
        self.markets = self._Markets(markets)
        self.orderbook = self._OrderBook(books)


# --- mapping -----------------------------------------------------------------

def test_to_order_book_maps_and_sorts():
    ml = _MLBook("tok", bids=[(0.43, 100), (0.44, 200)], asks=[(0.47, 120), (0.46, 80)])
    ob = to_order_book(ml)
    assert ob.best_bid == 0.44 and ob.best_ask == 0.46    # sorted best-first
    assert ob.token_id == "tok"
    # feeds our microstructure unchanged
    micro = compute_microstructure(ob)
    assert micro["mid"] == 0.45 and micro["spread_bps"] > 0


def test_to_order_book_none_on_empty():
    assert to_order_book(None) is None
    assert to_order_book(_MLBook("t", [], [])) is None


# --- graceful no-key ---------------------------------------------------------

def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("MARKETLENS_API_KEY", raising=False)
    assert MarketLensClient().enabled is False


# --- resolved sampling via injected client -----------------------------------

def test_sample_resolved_l2_with_injected_client():
    books = {"m1": _MLBook("m1", [(0.6, 50)], [(0.62, 40)]),
             "m2": _MLBook("m2", [(0.3, 90)], [(0.33, 70)])}
    fake = _FakeML([_Market("m1"), _Market("m2"), _Market("m3")], books)
    client = MarketLensClient(api_key="x", client=fake)

    samples = client.sample_resolved_l2(n=2)
    assert len(samples) == 2
    assert samples[0].market_id == "m1"
    assert samples[0].book.best_bid == 0.6
    assert samples[0].winning_outcome == "Yes"
    assert samples[0].as_of == "2026-04-15T02:00:00Z"
