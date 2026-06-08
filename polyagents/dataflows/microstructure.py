"""L2 order-book microstructure — inspired by MarketLens.

MarketLens models Polymarket's CLOB at tick / L2 depth with queue simulation.
We don't reimplement its tick engine, but we extract the microstructure
features that matter for a binary-priced book from the public ``/book``
snapshot: a size-weighted micro-price, multi-level depth imbalance, spread in
basis points, book pressure, and a first-cut queue estimate (size resting at
the touch — i.e. how much sits ahead of a join order).

All functions are pure over an :class:`OrderBook`, so they unit-test without the
network.
"""
from __future__ import annotations

from typing import Any

from .types import OrderBook, OrderBookLevel


def _depth(levels: list[OrderBookLevel], n: int) -> float:
    return sum(l.size for l in levels[:n])


def _imbalance(bid_depth: float, ask_depth: float) -> float:
    total = bid_depth + ask_depth
    return (bid_depth - ask_depth) / total if total else 0.0


def compute_microstructure(book: OrderBook, levels: tuple[int, ...] = (1, 3, 5)) -> dict[str, Any]:
    """Microstructure features from an L2 snapshot.

    ``micro_price`` is the standard size-weighted touch price
    ``(bid*ask_size + ask*bid_size) / (bid_size + ask_size)`` — it leans toward
    the side with *less* size, the direction price tends to move.
    """
    best_bid = book.best_bid
    best_ask = book.best_ask
    mid = book.mid
    spread = book.spread

    bid_touch = book.bids[0].size if book.bids else 0.0
    ask_touch = book.asks[0].size if book.asks else 0.0

    micro_price = None
    if best_bid is not None and best_ask is not None and (bid_touch + ask_touch) > 0:
        micro_price = (best_bid * ask_touch + best_ask * bid_touch) / (bid_touch + ask_touch)

    data: dict[str, Any] = {
        "available": True,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "micro_price": micro_price,
        "spread": spread,
        "spread_bps": (spread / mid * 10_000) if (spread is not None and mid) else None,
        "queue_ahead_bid": bid_touch,   # tokens resting at best bid (queue to join)
        "queue_ahead_ask": ask_touch,
        "n_bid_levels": len(book.bids),
        "n_ask_levels": len(book.asks),
    }

    # Multi-level depth + imbalance (MarketLens-style book pressure read).
    for n in levels:
        bid_d = _depth(book.bids, n)
        ask_d = _depth(book.asks, n)
        data[f"bid_depth_L{n}"] = bid_d
        data[f"ask_depth_L{n}"] = ask_d
        data[f"imbalance_L{n}"] = _imbalance(bid_d, ask_d)

    # Book pressure: deepest-level imbalance is the headline directional read.
    deepest = max(levels)
    data["book_pressure"] = data[f"imbalance_L{deepest}"]
    return data


def format_microstructure_report(data: dict[str, Any]) -> str:
    if not data.get("available"):
        return "Order book unavailable."
    mid = data.get("mid")
    micro = data.get("micro_price")
    spread_bps = data.get("spread_bps")
    pressure = data.get("book_pressure", 0.0)
    parts = [
        f"Order book: bid {data.get('best_bid')} / ask {data.get('best_ask')}",
    ]
    if mid is not None:
        parts.append(f"mid {mid:.3f}")
    if micro is not None:
        parts.append(f"micro {micro:.3f}")
    if spread_bps is not None:
        parts.append(f"spread {spread_bps:.0f}bps")
    head = ", ".join(parts) + "."
    body = (
        f" Depth L5 bid {data.get('bid_depth_L5', 0):,.0f} vs ask {data.get('ask_depth_L5', 0):,.0f} "
        f"(book pressure {pressure:+.0%}); queue@touch bid {data.get('queue_ahead_bid', 0):,.0f} / "
        f"ask {data.get('queue_ahead_ask', 0):,.0f}."
    )
    return head + body
