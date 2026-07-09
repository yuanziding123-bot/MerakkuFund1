"""PIT-safe feature construction for historical Lab collections."""
from __future__ import annotations

from datetime import datetime, time, timezone
from email.utils import parsedate_to_datetime

from polyagents.dataflows.features import extract_features
from polyagents.dataflows.sentiment import LexiconSentimentScorer, SentimentScorer, aggregate_sentiment
from polyagents.dataflows.types import Candle
from polyagents.dataflows.utils import parse_iso


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def build_price_raw(
    candles: list[Candle],
    *,
    available_at: datetime,
    trades_flow: dict | None = None,
    news: dict | None = None,
) -> dict:
    """Build raw collector-like data using only PIT-safe price candles."""
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    first, last = closes[0], closes[-1]
    pct_change = ((last - first) / first) if first else 0.0
    available = _iso(available_at)
    raw = {
        "price": {
            "last_price": last,
            "high": max(highs),
            "low": min(lows),
            "pct_change": pct_change,
            "closes": closes,
            "available_at": available,
        },
        "volume": {
            "total_volume": sum(float(c.volume or 0.0) for c in candles),
            "recent_5bar_volume": 0.0,
            "baseline_avg_volume": 0.0,
            "available_at": available,
        },
        "orderbook": {
            "book_pressure": 0.0,
            "spread_bps": 0.0,
            "micro_price": None,
            "mid": None,
            "available_at": available,
        },
        "trades_flow": trades_flow or {
            "flow_imbalance": 0.0,
            "n_trades": 0,
            "n_buys": 0,
            "n_sells": 0,
            "buy_notional": 0.0,
            "sell_notional": 0.0,
            "available_at": available,
            "source": "unavailable",
        },
        "news": news or {
            "sentiment": {"n_scored": 0, "mean": 0.0, "label": "neutral", "scores": []},
            "available_at": available,
            "items": [],
            "source": "unavailable",
        },
    }
    raw["features"] = extract_features(raw)
    raw["features"]["available_at"] = available
    return raw


def _published_available_at(value: str | None) -> datetime | None:
    """Return a conservative availability timestamp for a news publish field."""
    if not value:
        return None
    text = str(value).strip()
    parsed = parse_iso(text)
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    if "T" not in text and len(text) <= 10:
        parsed = datetime.combine(parsed.date(), time.max, tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_historical_news_sentiment(
    items: list,
    *,
    prediction_time: datetime,
    scorer: SentimentScorer | None = None,
) -> dict:
    """Build PIT-safe news sentiment from historically published items only."""
    scorer = scorer or LexiconSentimentScorer()
    pred = prediction_time.astimezone(timezone.utc)
    used: list[dict] = []
    skipped_no_published = 0
    skipped_future = 0
    available_at_max: datetime | None = None
    for item in items:
        title = str(getattr(item, "title", "") or "")
        snippet = str(getattr(item, "snippet", "") or "")
        published = getattr(item, "published", None)
        available_at = _published_available_at(published)
        if available_at is None:
            skipped_no_published += 1
            continue
        if available_at > pred:
            skipped_future += 1
            continue
        text = f"{title}. {snippet}"
        used.append(
            {
                "title": title,
                "url": str(getattr(item, "url", "") or ""),
                "snippet": snippet,
                "published": published,
                "available_at": _iso(available_at),
                "sentiment": scorer.score(text),
            }
        )
        available_at_max = max(available_at_max, available_at) if available_at_max else available_at
    sentiment = aggregate_sentiment([f"{i['title']}. {i['snippet']}" for i in used], scorer)
    return {
        "sentiment": sentiment,
        "available_at": _iso(available_at_max) if available_at_max else None,
        "items": used,
        "source": "historical_news",
        "pit_status": "clean",
        "n_items": len(used),
        "skipped_no_published": skipped_no_published,
        "skipped_future": skipped_future,
    }


def build_historical_trades_flow(
    trades: list[dict],
    *,
    token_id: str,
    min_ts: int,
    max_ts: int,
    available_at: datetime,
) -> dict:
    """Rebuild YES-side trade flow using only trades before prediction_time."""
    buy_notional = sell_notional = 0.0
    buys = sells = 0
    for trade in trades:
        if str(trade.get("asset") or "") != token_id:
            continue
        ts = trade.get("timestamp")
        if not isinstance(ts, (int, float)) or not (min_ts <= int(ts) < max_ts):
            continue
        try:
            size = float(trade.get("size"))
            price = float(trade.get("price"))
        except (TypeError, ValueError):
            continue
        notional = size * price
        if str(trade.get("side") or "").upper() == "SELL":
            sell_notional += notional
            sells += 1
        else:
            buy_notional += notional
            buys += 1
    total = buy_notional + sell_notional
    return {
        "lookback_start_ts": min_ts,
        "lookback_end_ts": max_ts,
        "n_trades": buys + sells,
        "n_buys": buys,
        "n_sells": sells,
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "flow_imbalance": (buy_notional - sell_notional) / total if total else 0.0,
        "available_at": _iso(available_at),
        "source": "historical_trades",
    }
