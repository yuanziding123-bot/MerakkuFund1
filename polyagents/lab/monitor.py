"""Dry-run opportunity monitor for Lab strategies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from polyagents.agents.decision_agent import decide
from polyagents.agents.schemas import Signal
from polyagents.dataflows.features import extract_features
from polyagents.dataflows.microstructure import compute_microstructure
from polyagents.dataflows.polymarket_client import PolymarketDataClient
from polyagents.dataflows.types import Candle, Market
from polyagents.default_config import DEFAULT_CONFIG

from .strategies import DEFAULT_STRATEGY_ID, get_strategy


@dataclass(frozen=True, kw_only=True)
class MonitorRequest:
    strategy_id: str = DEFAULT_STRATEGY_ID
    limit: int = 20
    min_volume_24h: float = 0.0
    min_edge: float | None = None
    include_holds: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.min_volume_24h < 0:
            raise ValueError("min_volume_24h must be non-negative")
        if self.min_edge is not None and self.min_edge < 0:
            raise ValueError("min_edge must be non-negative")
        get_strategy(self.strategy_id)


@dataclass(frozen=True, kw_only=True)
class MonitorOpportunity:
    market_token_id: str
    question: str
    strategy_id: str
    p_raw: float
    p_cal: float
    market_price: float
    edge: float
    apy: float
    action: str
    size_usdc: float
    dry_run: bool = True
    reasons: list[str] = field(default_factory=list)
    market: dict[str, Any] = field(default_factory=dict)
    signal_model: dict[str, Any] = field(default_factory=dict)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _price_raw(candles: list[Candle], fallback_price: float, available_at: str) -> dict[str, Any]:
    closes = [float(c.close) for c in candles if c.close is not None]
    if not closes:
        closes = [float(fallback_price)]
    first = closes[0]
    last = closes[-1]
    return {
        "last_price": last,
        "high": max(closes),
        "low": min(closes),
        "pct_change": ((last - first) / first) if first else 0.0,
        "closes": closes,
        "available_at": available_at,
    }


def _trade_flow_raw(trades: list[dict], token_id: str, available_at: str) -> dict[str, Any]:
    buy_notional = sell_notional = 0.0
    buys = sells = 0
    for trade in trades:
        if str(trade.get("asset") or "") != token_id:
            continue
        try:
            notional = float(trade.get("size")) * float(trade.get("price"))
        except (TypeError, ValueError):
            continue
        if str(trade.get("side") or "").upper() == "SELL":
            sell_notional += notional
            sells += 1
        else:
            buy_notional += notional
            buys += 1
    total = buy_notional + sell_notional
    return {
        "n_trades": buys + sells,
        "n_buys": buys,
        "n_sells": sells,
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "flow_imbalance": (buy_notional - sell_notional) / total if total else 0.0,
        "available_at": available_at,
        "source": "live_trades",
    }


def build_live_raw(client: Any, market: Market, *, config: dict | None = None) -> tuple[dict[str, Any], float, float | None]:
    """Build a current read-only feature bundle for an active market side."""
    cfg = config or DEFAULT_CONFIG
    available_at = _iso_now()
    candles = client.fetch_price_history(
        market.token_id,
        interval=cfg.get("price_interval", "1w"),
        fidelity=int(cfg.get("price_fidelity", 60)),
    )
    raw: dict[str, Any] = {
        "price": _price_raw(candles, market.price, available_at),
        "volume": {
            "total_volume": sum(float(c.volume or 0.0) for c in candles),
            "recent_5bar_volume": sum(float(c.volume or 0.0) for c in candles[-5:]),
            "baseline_avg_volume": (
                sum(float(c.volume or 0.0) for c in candles) / len(candles)
                if candles
                else 0.0
            ),
            "available_at": available_at,
        },
        "orderbook": {
            "book_pressure": 0.0,
            "spread_bps": None,
            "micro_price": None,
            "mid": None,
            "available_at": available_at,
            "source": "unavailable",
        },
        "trades_flow": {
            "flow_imbalance": 0.0,
            "n_trades": 0,
            "available_at": available_at,
            "source": "unavailable",
        },
        "news": {
            "sentiment": {"mean": 0.0},
            "available_at": available_at,
            "source": "not_reconstructed_for_monitor_mvp",
        },
    }
    live_price = float(market.price)
    spread_bps: float | None = None

    book = client.fetch_order_book(market.token_id)
    if book is not None:
        micro = compute_microstructure(book)
        raw["orderbook"] = {**micro, "available_at": available_at, "source": "live_orderbook"}
        live_price = float(micro.get("mid") or live_price)
        spread_bps = micro.get("spread_bps")
    else:
        spread_bps = float(market.spread or 0.0) * 10_000 if market.spread else None

    trades = client.fetch_market_trades(market.condition_id, max_pages=1)
    if trades:
        raw["trades_flow"] = _trade_flow_raw(trades, market.token_id, available_at)

    raw["features"] = extract_features(raw)
    raw["features"]["available_at"] = available_at
    return raw, live_price, spread_bps


def score_market_opportunity(
    market: Market,
    *,
    raw: dict[str, Any],
    market_price: float,
    spread_bps: float | None,
    strategy_id: str,
    config: dict,
) -> MonitorOpportunity:
    strategy = get_strategy(strategy_id)
    signal_model = strategy.predict(raw, market_price)
    p_raw = float(signal_model["p_raw"])
    direction = "yes" if p_raw >= market_price else "no"
    signal = Signal(
        direction=direction,
        p_true=p_raw,
        conviction="medium",
        rationale=f"dry-run monitor strategy {strategy.id}",
    )
    decision = decide(
        signal,
        market_price,
        float(market.liquidity or 0.0),
        spread_bps,
        config,
        days_to_expiry=float(market.days_to_expiry or 30.0),
    )
    return MonitorOpportunity(
        market_token_id=market.token_id,
        question=market.question,
        strategy_id=strategy.id,
        p_raw=p_raw,
        p_cal=float(decision.p_true),
        market_price=float(decision.market_price),
        edge=float(decision.edge),
        apy=float(decision.annualized_edge),
        action=decision.action,
        size_usdc=float(decision.size_usdc),
        dry_run=True,
        reasons=list(decision.reasons),
        market={
            "condition_id": market.condition_id,
            "outcome": market.outcome,
            "volume_24h": float(market.volume_24h or 0.0),
            "liquidity": float(market.liquidity or 0.0),
            "days_to_expiry": float(market.days_to_expiry or 0.0),
        },
        signal_model=signal_model,
    )


class LabMonitor:
    """Scan active markets with a Lab strategy, always dry-run only."""

    def __init__(self, client: Any | None = None, config: dict | None = None) -> None:
        self.config = dict(config or DEFAULT_CONFIG)
        self.client = client or PolymarketDataClient.from_config(self.config)

    def scan(self, request: MonitorRequest) -> dict[str, Any]:
        raw_markets = self.client.list_active_markets(limit=max(request.limit * 4, request.limit))
        markets = self.client.to_markets(raw_markets)
        opportunities: list[MonitorOpportunity] = []
        errors: list[dict[str, str]] = []
        for market in markets:
            if len(opportunities) >= request.limit:
                break
            if not (0.01 < float(market.price) < 0.99):
                continue
            if float(market.volume_24h or 0.0) < request.min_volume_24h:
                continue
            try:
                raw, market_price, spread_bps = build_live_raw(self.client, market, config=self.config)
                opp = score_market_opportunity(
                    market,
                    raw=raw,
                    market_price=market_price,
                    spread_bps=spread_bps,
                    strategy_id=request.strategy_id,
                    config=self.config,
                )
            except Exception as exc:
                errors.append({"market_token_id": market.token_id, "error": str(exc)})
                continue
            if request.min_edge is not None and abs(opp.edge) < request.min_edge:
                continue
            if opp.action == "hold" and not request.include_holds:
                continue
            opportunities.append(opp)

        opportunities.sort(key=lambda o: (o.action == "buy", o.edge, o.apy), reverse=True)
        return {
            "strategy_id": request.strategy_id,
            "dry_run": True,
            "n": len(opportunities),
            "opportunities": [asdict(o) for o in opportunities],
            "message": "no opportunity" if not opportunities else "ok",
            "errors": errors,
        }
