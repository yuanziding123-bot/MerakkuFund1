"""Build historical settled-market collections for Lab replay."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil

from polyagents.dataflows.types import Candle, Market
from polyagents.dataflows.utils import parse_iso, parse_json_field

from .feature_builder import build_historical_news_sentiment, build_historical_trades_flow, build_price_raw


@dataclass(frozen=True, kw_only=True)
class SettledMarket:
    market_id: str
    condition_id: str
    question: str
    yes_token_id: str
    outcome_label: str
    outcome: int
    resolution_time: datetime
    final_yes_price: float
    raw: dict

    def to_market(self) -> Market:
        return Market(
            market_id=self.market_id,
            condition_id=self.condition_id,
            question=self.question,
            description=str(self.raw.get("description") or ""),
            outcome="YES",
            token_id=self.yes_token_id,
            price=self.final_yes_price,
            volume_24h=float(self.raw.get("volume24hr") or 0.0),
            liquidity=float(self.raw.get("liquidityNum") or self.raw.get("liquidity") or 0.0),
            spread=float(self.raw.get("spread") or 0.0),
            days_to_expiry=0.0,
            expiry=self.resolution_time,
            raw=self.raw,
        )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _settled_market_from_outcome(
    raw: dict,
    *,
    idx: int,
    label: str,
    token_id: str,
    final_price: float,
    resolution: datetime,
    include_label_in_question: bool,
) -> SettledMarket:
    base_question = str(raw.get("question") or "")
    question = f"{base_question} [{label}]" if include_label_in_question else base_question
    raw_for_token = {
        **raw,
        "lab_outcome_label": label,
        "lab_outcome_index": idx,
        "lab_token_id": token_id,
        "lab_news_query": base_question,
    }
    return SettledMarket(
        market_id=f"{raw.get('id') or raw.get('conditionId') or ''}:{idx}",
        condition_id=str(raw.get("conditionId") or ""),
        question=question,
        yes_token_id=token_id,
        outcome_label=label,
        outcome=1 if final_price >= 0.5 else 0,
        resolution_time=resolution,
        final_yes_price=final_price,
        raw=raw_for_token,
    )


def parse_settled_outcome_markets(raw: dict) -> tuple[list[SettledMarket], str | None]:
    outcome_labels = [str(x).strip() for x in parse_json_field(raw.get("outcomes"))]
    outcomes = [label.lower() for label in outcome_labels]
    token_ids = [str(x) for x in parse_json_field(raw.get("clobTokenIds") or [])]
    prices_raw = parse_json_field(raw.get("outcomePrices") or [])
    if len(outcomes) < 2 or len(token_ids) != len(outcomes):
        return [], "skipped_non_binary"
    if len(prices_raw) != len(outcomes):
        return [], "skipped_no_outcome"
    try:
        prices = [float(p) for p in prices_raw]
    except (TypeError, ValueError):
        return [], "skipped_no_outcome"
    resolution = parse_iso(raw.get("endDate") or raw.get("end_date_iso") or raw.get("closedTime"))
    if resolution is None:
        return [], "skipped_no_outcome"

    if len(outcomes) == 2 and "yes" in outcomes and "no" in outcomes:
        yes_idx = outcomes.index("yes")
        return [
            _settled_market_from_outcome(
                raw,
                idx=yes_idx,
                label=outcome_labels[yes_idx],
                token_id=token_ids[yes_idx],
                final_price=prices[yes_idx],
                resolution=resolution,
                include_label_in_question=False,
            )
        ], None

    markets = [
        _settled_market_from_outcome(
            raw,
            idx=idx,
            label=outcome_labels[idx],
            token_id=token_ids[idx],
            final_price=prices[idx],
            resolution=resolution,
            include_label_in_question=True,
        )
        for idx, label in enumerate(outcomes)
    ]
    return markets, None


def parse_settled_binary_market(raw: dict) -> tuple[SettledMarket | None, str | None]:
    outcomes = [str(x).strip().lower() for x in parse_json_field(raw.get("outcomes"))]
    if len(outcomes) != 2 or "yes" not in outcomes or "no" not in outcomes:
        return None, "skipped_non_binary"
    markets, reason = parse_settled_outcome_markets(raw)
    return (markets[0], None) if markets else (None, reason)


def select_prediction_windows(
    candles: list[Candle],
    *,
    resolution_time: datetime,
    min_history: int = 4,
    prediction_policy: str = "midpoint",
) -> list[tuple[datetime, list[Candle], float, dict]]:
    ordered = sorted([c for c in candles if c.ts < resolution_time], key=lambda c: c.ts)
    if len(ordered) < min_history + 1:
        return []
    if prediction_policy == "midpoint":
        candidates = [(len(ordered) // 2, "midpoint", 0.5)]
    elif prediction_policy in {"multi", "multi-default"}:
        candidates = [
            (ceil((len(ordered) - 1) * frac), f"fraction_{frac:g}", frac)
            for frac in (0.25, 0.5, 0.75)
        ]
    else:
        raise ValueError(f"unsupported prediction_policy: {prediction_policy}")

    selected = []
    seen: set[int] = set()
    for raw_idx, label, fraction in candidates:
        idx = min(max(int(raw_idx), min_history), len(ordered) - 1)
        if idx in seen:
            continue
        seen.add(idx)
        prediction_time = ordered[idx].ts
        pit_candles = [c for c in ordered[:idx] if c.ts < prediction_time]
        if len(pit_candles) < min_history:
            continue
        market_price = float(pit_candles[-1].close)
        if not (0.0 <= market_price <= 1.0):
            continue
        selected.append(
            (
                prediction_time,
                pit_candles,
                market_price,
                {"prediction_label": label, "prediction_fraction": fraction, "prediction_index": idx},
            )
        )
    return selected


def select_prediction_window(
    candles: list[Candle],
    *,
    resolution_time: datetime,
    min_history: int = 4,
) -> tuple[datetime, list[Candle], float] | None:
    selected = select_prediction_windows(
        candles,
        resolution_time=resolution_time,
        min_history=min_history,
        prediction_policy="midpoint",
    )
    if not selected:
        return None
    prediction_time, pit_candles, market_price, _meta = selected[0]
    return prediction_time, pit_candles, market_price


def build_historical_collection(
    market: SettledMarket,
    candles: list[Candle],
    *,
    trades: list[dict] | None = None,
    news_client=None,
    news_max_results: int = 5,
    min_history: int = 4,
    prediction_policy: str = "midpoint",
) -> tuple[dict | None, str | None]:
    selected = select_prediction_windows(
        candles,
        resolution_time=market.resolution_time,
        min_history=min_history,
        prediction_policy=prediction_policy,
    )
    if not selected:
        return None, "skipped_no_price_history"
    prediction_time, pit_candles, market_price, prediction_meta = selected[0]
    return _build_historical_collection_for_window(
        market,
        prediction_time=prediction_time,
        pit_candles=pit_candles,
        market_price=market_price,
        prediction_policy=prediction_policy,
        prediction_meta=prediction_meta,
        trades=trades,
        news_client=news_client,
        news_max_results=news_max_results,
    )


def build_historical_collections(
    market: SettledMarket,
    candles: list[Candle],
    *,
    trades: list[dict] | None = None,
    news_client=None,
    news_max_results: int = 5,
    min_history: int = 4,
    prediction_policy: str = "multi",
) -> tuple[list[dict], str | None]:
    selected = select_prediction_windows(
        candles,
        resolution_time=market.resolution_time,
        min_history=min_history,
        prediction_policy=prediction_policy,
    )
    if not selected:
        return [], "skipped_no_price_history"
    collections: list[dict] = []
    for prediction_time, pit_candles, market_price, prediction_meta in selected:
        collection, reason = _build_historical_collection_for_window(
            market,
            prediction_time=prediction_time,
            pit_candles=pit_candles,
            market_price=market_price,
            prediction_policy=prediction_policy,
            prediction_meta=prediction_meta,
            trades=trades,
            news_client=news_client,
            news_max_results=news_max_results,
        )
        if collection is None:
            return [], reason
        collections.append(collection)
    return collections, None


def _build_historical_collection_for_window(
    market: SettledMarket,
    *,
    prediction_time: datetime,
    pit_candles: list[Candle],
    market_price: float,
    prediction_policy: str,
    prediction_meta: dict,
    trades: list[dict] | None = None,
    news_client=None,
    news_max_results: int = 5,
) -> tuple[dict | None, str | None]:
    available_at = pit_candles[-1].ts
    if available_at >= prediction_time or prediction_time >= market.resolution_time:
        return None, "skipped_pit"
    trades_flow = build_historical_trades_flow(
        trades or [],
        token_id=market.yes_token_id,
        min_ts=int(pit_candles[0].ts.timestamp()),
        max_ts=int(prediction_time.timestamp()),
        available_at=available_at,
    )
    news = None
    if news_client is not None and getattr(news_client, "enabled", False):
        news_items = news_client.search_between(
            str(market.raw.get("lab_news_query") or market.question),
            start=pit_candles[0].ts,
            end=prediction_time,
            max_results=news_max_results,
        )
        news = build_historical_news_sentiment(news_items, prediction_time=prediction_time)
    raw = build_price_raw(pit_candles, available_at=available_at, trades_flow=trades_flow, news=news)
    availability = [available_at]
    news_available = parse_iso((news or {}).get("available_at"))
    if news_available is not None:
        availability.append(news_available)
    available_at_max = max(availability)
    raw["available_at_max"] = _iso(available_at_max)
    raw["lab"] = {
        "outcome": market.outcome,
        "p_market": market_price,
        "available_at_max": _iso(available_at_max),
        "ingestion_source": "polymarket_closed_markets",
        "prediction_policy": prediction_policy,
        **prediction_meta,
        "resolution_time": _iso(market.resolution_time),
    }
    raw["market"] = {
        "market_id": market.market_id,
        "condition_id": market.condition_id,
        "yes_token_id": market.yes_token_id,
        "outcome_label": market.outcome_label,
        "outcome_index": market.raw.get("lab_outcome_index"),
        "final_yes_price": market.final_yes_price,
        "volume_24h": float(market.raw.get("volume24hr") or 0.0),
        "liquidity": float(market.raw.get("liquidityNum") or market.raw.get("liquidity") or 0.0),
        "spread": float(market.raw.get("spread") or 0.0),
        "polymarket_price_source": "clob_prices_history",
        "polymarket_outcome_source": "gamma_outcomePrices",
        "available_at": _iso(available_at),
    }
    return {
        "token_id": market.yes_token_id,
        "as_of": _iso(prediction_time),
        "question": market.question,
        "market_price": market_price,
        "raw": raw,
    }, None
