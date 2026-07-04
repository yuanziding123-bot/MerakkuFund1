"""Build historical settled-market collections for Lab replay."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from polyagents.dataflows.types import Candle, Market
from polyagents.dataflows.utils import parse_iso, parse_json_field

from .feature_builder import build_historical_trades_flow, build_price_raw


@dataclass(frozen=True, kw_only=True)
class SettledMarket:
    market_id: str
    condition_id: str
    question: str
    yes_token_id: str
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


def parse_settled_binary_market(raw: dict) -> tuple[SettledMarket | None, str | None]:
    outcomes = [str(x).strip().lower() for x in parse_json_field(raw.get("outcomes"))]
    token_ids = [str(x) for x in parse_json_field(raw.get("clobTokenIds") or [])]
    prices_raw = parse_json_field(raw.get("outcomePrices") or [])
    if len(outcomes) != 2 or len(token_ids) != 2:
        return None, "skipped_non_binary"
    if "yes" not in outcomes or "no" not in outcomes:
        return None, "skipped_non_binary"
    if len(prices_raw) != 2:
        return None, "skipped_no_outcome"
    try:
        prices = [float(p) for p in prices_raw]
    except (TypeError, ValueError):
        return None, "skipped_no_outcome"
    yes_idx = outcomes.index("yes")
    resolution = parse_iso(raw.get("endDate") or raw.get("end_date_iso") or raw.get("closedTime"))
    if resolution is None:
        return None, "skipped_no_outcome"
    final_yes = prices[yes_idx]
    return SettledMarket(
        market_id=str(raw.get("id") or raw.get("conditionId") or ""),
        condition_id=str(raw.get("conditionId") or ""),
        question=str(raw.get("question") or ""),
        yes_token_id=token_ids[yes_idx],
        outcome=1 if final_yes >= 0.5 else 0,
        resolution_time=resolution,
        final_yes_price=final_yes,
        raw=raw,
    ), None


def select_prediction_window(
    candles: list[Candle],
    *,
    resolution_time: datetime,
    min_history: int = 4,
) -> tuple[datetime, list[Candle], float] | None:
    ordered = sorted([c for c in candles if c.ts < resolution_time], key=lambda c: c.ts)
    if len(ordered) < min_history + 1:
        return None
    idx = min(max(len(ordered) // 2, min_history), len(ordered) - 1)
    prediction_time = ordered[idx].ts
    pit_candles = [c for c in ordered[:idx] if c.ts < prediction_time]
    if len(pit_candles) < min_history:
        return None
    market_price = float(pit_candles[-1].close)
    if not (0.0 <= market_price <= 1.0):
        return None
    return prediction_time, pit_candles, market_price


def build_historical_collection(
    market: SettledMarket,
    candles: list[Candle],
    *,
    trades: list[dict] | None = None,
    min_history: int = 4,
    prediction_policy: str = "midpoint",
) -> tuple[dict | None, str | None]:
    if prediction_policy != "midpoint":
        raise ValueError(f"unsupported prediction_policy: {prediction_policy}")
    selected = select_prediction_window(
        candles,
        resolution_time=market.resolution_time,
        min_history=min_history,
    )
    if selected is None:
        return None, "skipped_no_price_history"
    prediction_time, pit_candles, market_price = selected
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
    raw = build_price_raw(pit_candles, available_at=available_at, trades_flow=trades_flow)
    raw["available_at_max"] = _iso(available_at)
    raw["lab"] = {
        "outcome": market.outcome,
        "p_market": market_price,
        "available_at_max": _iso(available_at),
        "ingestion_source": "polymarket_closed_markets",
        "prediction_policy": prediction_policy,
        "resolution_time": _iso(market.resolution_time),
    }
    raw["market"] = {
        "market_id": market.market_id,
        "condition_id": market.condition_id,
        "yes_token_id": market.yes_token_id,
        "final_yes_price": market.final_yes_price,
        "available_at": _iso(available_at),
    }
    return {
        "token_id": market.yes_token_id,
        "as_of": _iso(prediction_time),
        "question": market.question,
        "market_price": market_price,
        "raw": raw,
    }, None
