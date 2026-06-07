"""Rebuild candle volume from /trades.

The prices-history endpoint returns price-only series. We reconstruct volume by
bucket-summing each trade's ``size`` (token units) into the candle whose
``[start, start+bar)`` window contains the trade timestamp. Only trades on the
matching ``asset`` (= the candle side's ``token_id``) are counted, so YES-side
fills don't inflate NO-side volume.

This is the cache-free variant of polymarket's ``src/data/volume.py`` — fine for
on-demand collection; a persistent trade cache can be layered in later.
"""
from __future__ import annotations

from .polymarket_client import PolymarketDataClient
from .types import Candle


def enrich_candles_with_volume(
    candles: list[Candle],
    condition_id: str,
    token_id: str,
    client: PolymarketDataClient,
) -> list[Candle]:
    """Return new candles with ``.volume`` populated. Short/empty markets pass through."""
    if not candles:
        return candles

    bar_seconds = _detect_bar_seconds(candles)
    if bar_seconds <= 0:
        return candles

    needed_lo = int(candles[0].ts.timestamp())
    raw_trades = client.fetch_market_trades(condition_id, min_ts=needed_lo)
    sizes = _flatten_sizes(raw_trades, token_id)
    if not sizes:
        return candles

    bucket_volumes = _bucket_sum(sizes, candles[0].ts.timestamp(), bar_seconds, len(candles))
    enriched = list(candles)
    for i, c in enumerate(enriched):
        if bucket_volumes[i] > 0:
            enriched[i] = Candle(
                ts=c.ts, open=c.open, high=c.high, low=c.low, close=c.close, volume=bucket_volumes[i]
            )
    return enriched


def _detect_bar_seconds(candles: list[Candle]) -> int:
    if len(candles) < 2:
        return 3600
    deltas = [
        int((candles[i + 1].ts - candles[i].ts).total_seconds())
        for i in range(min(10, len(candles) - 1))
    ]
    deltas = [d for d in deltas if d > 0]
    return min(deltas) if deltas else 3600


def _flatten_sizes(raw_trades: list[dict], token_id: str) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for t in raw_trades:
        if str(t.get("asset") or "") != token_id:
            continue
        ts = t.get("timestamp")
        size = t.get("size")
        if not ts or size is None:
            continue
        try:
            out.append((int(ts), float(size)))
        except (ValueError, TypeError):
            continue
    return out


def _bucket_sum(
    trades: list[tuple[int, float]],
    first_ts_seconds: float,
    bar_seconds: int,
    n_buckets: int,
) -> list[float]:
    buckets = [0.0] * n_buckets
    base = int(first_ts_seconds)
    for ts, size in trades:
        idx = (ts - base) // bar_seconds
        if 0 <= idx < n_buckets:
            buckets[idx] += size
    return buckets
