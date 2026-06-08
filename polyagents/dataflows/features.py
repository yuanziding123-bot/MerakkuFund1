"""Deterministic factor extraction — inspired by Alpha DevBox.

Alpha DevBox turns a natural-language idea into statistical factor analysis. The
LLM-driven research loop belongs to the strategy layer; what belongs *here* is
the deterministic half: consolidating every collector's structured output into
one flat, named factor vector that downstream models / sizing consume. This is
the join point of Layer 1 — price, volume, microstructure, flow, and sentiment
become a single feature record, plus an optional Kronos forecast.

Pure function over the ``raw`` dict the collectors fill, so it unit-tests with a
hand-built ``raw``.
"""
from __future__ import annotations

from typing import Any

from .forecaster import CandleForecaster, NullForecaster


def _get(raw: dict, source: str, key: str, default=0.0):
    section = raw.get(source) or {}
    val = section.get(key, default)
    return val if val is not None else default


def extract_features(raw: dict[str, Any], forecaster: CandleForecaster | None = None) -> dict[str, Any]:
    """Flatten collector outputs into named factors. Missing sources default to 0."""
    forecaster = forecaster or NullForecaster()

    last = _get(raw, "price", "last_price")
    high = _get(raw, "price", "high")
    low = _get(raw, "price", "low")
    baseline = _get(raw, "volume", "baseline_avg_volume")
    recent = _get(raw, "volume", "recent_5bar_volume")
    micro = _get(raw, "orderbook", "micro_price", default=None)
    mid = _get(raw, "orderbook", "mid", default=None)
    news_sentiment = ((raw.get("news") or {}).get("sentiment") or {})

    factors: dict[str, float] = {
        # price
        "price_momentum": _get(raw, "price", "pct_change"),
        "price_range": ((high - low) / last) if last else 0.0,
        # volume
        "volume_total": _get(raw, "volume", "total_volume"),
        "volume_spike_ratio": (recent / 5 / baseline) if baseline else 0.0,
        # microstructure (MarketLens)
        "book_pressure": _get(raw, "orderbook", "book_pressure"),
        "spread_bps": _get(raw, "orderbook", "spread_bps", default=0.0),
        "micro_vs_mid": (micro - mid) if (micro is not None and mid is not None) else 0.0,
        # flow
        "flow_imbalance": _get(raw, "trades_flow", "flow_imbalance"),
        "trade_count": float(_get(raw, "trades_flow", "n_trades", 0)),
        # sentiment (FinGPT)
        "sentiment": float(news_sentiment.get("mean", 0.0)) if isinstance(news_sentiment, dict) else 0.0,
    }

    data: dict[str, Any] = {"factors": factors, "vector": list(factors.values()), "names": list(factors.keys())}

    # Kronos seam: only present if a real forecaster is plugged in.
    forecast = forecaster.forecast(list(_get(raw, "price", "closes", default=[]) or []))
    if forecast is not None:
        data["forecast"] = forecast

    return data


def format_features_report(data: dict[str, Any]) -> str:
    factors = data.get("factors", {})
    if not factors:
        return "No features extracted."
    headline = [
        f"momentum {factors.get('price_momentum', 0):+.1%}",
        f"book {factors.get('book_pressure', 0):+.0%}",
        f"flow {factors.get('flow_imbalance', 0):+.0%}",
        f"sentiment {factors.get('sentiment', 0):+.2f}",
        f"vol-spike {factors.get('volume_spike_ratio', 0):.2f}x",
    ]
    text = "Factors: " + ", ".join(headline) + "."
    if "forecast" in data:
        text += f" Forecast: {data['forecast']}."
    return text
