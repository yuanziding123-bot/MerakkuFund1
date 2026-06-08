"""High-level data interface — the "tools" the graph nodes call.

Each function fetches from :class:`PolymarketDataClient` (or the news client)
and returns ``(report_text, data)``:

  * ``report_text`` — a compact human-readable summary, the kind a downstream
    LLM analyst reads (mirrors TradingAgents' string-returning dataflows).
  * ``data`` — structured numbers, because Polymarket's detectors / sizing need
    the figures, not just prose.

Keeping fetch+format here (rather than in the nodes) keeps the graph layer thin
and lets these be unit-tested against a fake client.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from .microstructure import compute_microstructure, format_microstructure_report
from .news import NewsClient
from .polymarket_client import PolymarketDataClient
from .sentiment import LexiconSentimentScorer, SentimentScorer, aggregate_sentiment
from .types import Candle, Market
from .utils import utcnow
from .volume import enrich_candles_with_volume


# ----- market identity (deterministic, no network) --------------------------

def get_market_context(market: Market) -> str:
    """A stable, human-readable identity block for the market under analysis.

    Resolved once at run start and threaded through state so every node shares
    one source of truth for *what* is being analysed (mirrors TradingAgents'
    ``instrument_context``).
    """
    return (
        f"Market: {market.question}\n"
        f"Outcome side: {market.outcome}  (token {market.token_id[:12]}…)\n"
        f"Condition: {market.condition_id}\n"
        f"Market price: {market.price:.3f}  |  24h volume: ${market.volume_24h:,.0f}  |  "
        f"liquidity: ${market.liquidity:,.0f}\n"
        f"Expiry: {market.expiry.isoformat()}  ({market.days_to_expiry:.1f} days out)"
    )


# ----- price + volume -------------------------------------------------------

def fetch_enriched_candles(
    client: PolymarketDataClient,
    token_id: str,
    condition_id: str,
    interval: str = "1h",
    fidelity: int = 60,
) -> list[Candle]:
    """Price-history candles with reconstructed volume."""
    candles = client.fetch_price_history(token_id, interval=interval, fidelity=fidelity)
    return enrich_candles_with_volume(candles, condition_id, token_id, client)


def format_price_report(candles: list[Candle]) -> tuple[str, dict[str, Any]]:
    if not candles:
        return "No price history available.", {"n_candles": 0}
    closes = [c.close for c in candles]
    first, last = closes[0], closes[-1]
    hi, lo = max(closes), min(closes)
    change = (last - first) / first if first else 0.0
    data = {
        "n_candles": len(candles),
        "first_price": first,
        "last_price": last,
        "high": hi,
        "low": lo,
        "pct_change": change,
        "start": candles[0].ts.isoformat(),
        "end": candles[-1].ts.isoformat(),
        # Trimmed close series — consumed by the Kronos-style forecaster hook.
        "closes": closes[-200:],
    }
    text = (
        f"Price history: {len(candles)} bars from {data['start']} to {data['end']}.\n"
        f"Last {last:.3f} (open {first:.3f}, {change:+.1%}); range [{lo:.3f}, {hi:.3f}]."
    )
    return text, data


def format_volume_report(candles: list[Candle]) -> tuple[str, dict[str, Any]]:
    vols = [c.volume for c in candles]
    total = sum(vols)
    nonzero = sum(1 for v in vols if v > 0)
    if not candles or total == 0:
        return "No reconstructed volume (illiquid or no trades in window).", {
            "total_volume": 0.0,
            "bars_with_volume": 0,
            "n_candles": len(candles),
        }
    recent = sum(vols[-5:])
    baseline = sum(vols[-65:-5]) / 60 if len(vols) >= 65 else 0.0
    data = {
        "total_volume": total,
        "bars_with_volume": nonzero,
        "n_candles": len(candles),
        "recent_5bar_volume": recent,
        "baseline_avg_volume": baseline,
    }
    text = (
        f"Volume: {total:,.0f} tokens across {nonzero}/{len(candles)} bars. "
        f"Last 5 bars {recent:,.0f}"
        + (f" vs baseline avg {baseline:,.1f}/bar." if baseline else ".")
    )
    return text, data


# ----- order book -----------------------------------------------------------

def get_orderbook_report(client: PolymarketDataClient, token_id: str) -> tuple[str, dict[str, Any]]:
    """L2 microstructure (MarketLens-inspired) from the public book snapshot."""
    book = client.fetch_order_book(token_id)
    if book is None:
        return "Order book unavailable.", {"available": False}
    data = compute_microstructure(book)
    return format_microstructure_report(data), data


# ----- trade flow -----------------------------------------------------------

def get_trades_flow_report(
    client: PolymarketDataClient,
    condition_id: str,
    token_id: str,
    lookback_hours: int = 24,
) -> tuple[str, dict[str, Any]]:
    min_ts = int((utcnow() - timedelta(hours=lookback_hours)).timestamp())
    raw = client.fetch_market_trades(condition_id, min_ts=min_ts)
    buy_notional = sell_notional = 0.0
    buys = sells = 0
    for t in raw:
        if str(t.get("asset") or "") != token_id:
            continue
        ts = t.get("timestamp")
        if not isinstance(ts, (int, float)) or ts < min_ts:
            continue
        try:
            size = float(t.get("size"))
            price = float(t.get("price"))
        except (TypeError, ValueError):
            continue
        notional = size * price
        if str(t.get("side") or "").upper() == "SELL":
            sell_notional += notional
            sells += 1
        else:
            buy_notional += notional
            buys += 1
    total = buy_notional + sell_notional
    imbalance = (buy_notional - sell_notional) / total if total else 0.0
    data = {
        "lookback_hours": lookback_hours,
        "n_trades": buys + sells,
        "n_buys": buys,
        "n_sells": sells,
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "flow_imbalance": imbalance,
    }
    if buys + sells == 0:
        return f"No trades in the last {lookback_hours}h for this side.", data
    text = (
        f"Trade flow ({lookback_hours}h): {buys + sells} trades, "
        f"buy ${buy_notional:,.0f} vs sell ${sell_notional:,.0f}, "
        f"flow imbalance {imbalance:+.0%}."
    )
    return text, data


# ----- news -----------------------------------------------------------------

def get_news_report(
    news_client: NewsClient,
    question: str,
    max_results: int = 5,
    scorer: SentimentScorer | None = None,
) -> tuple[str, dict[str, Any]]:
    scorer = scorer or LexiconSentimentScorer()
    neutral = {"n_scored": 0, "mean": 0.0, "label": "neutral", "scores": []}
    if not news_client.enabled:
        return "News disabled (no TAVILY_API_KEY).", {"enabled": False, "n_items": 0, "sentiment": neutral}
    items = news_client.search(question, max_results=max_results)
    sentiment = aggregate_sentiment([f"{i.title}. {i.snippet}" for i in items], scorer)
    data = {
        "enabled": True,
        "n_items": len(items),
        "sentiment": sentiment,
        "items": [
            {"title": i.title, "url": i.url, "published": i.published, "sentiment": scorer.score(f"{i.title}. {i.snippet}")}
            for i in items
        ],
    }
    if not items:
        return f"No recent news found for: {question}", data
    lines = [f"News for: {question}  (sentiment: {sentiment['label']} {sentiment['mean']:+.2f})"]
    for i in items:
        when = f" ({i.published})" if i.published else ""
        lines.append(f"- {i.title}{when}: {i.snippet}")
    return "\n".join(lines), data
