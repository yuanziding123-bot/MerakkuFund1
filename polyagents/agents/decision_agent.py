"""Decision agent — deterministic edge / Kelly / risk gates.

Reads the Signal (LLM probability read) plus market microstructure from the
state and produces a :class:`TradeDecision`. No LLM here: sizing and risk are
math so they're auditable and reproducible (the v3.0 plan embeds risk in the
decision agent rather than spinning up a separate risk Agent).
"""
from __future__ import annotations

from typing import Any, Callable

from .risk import edge_for_side, effective_market_price, kelly_fraction
from .schemas import Signal, TradeDecision

Node = Callable[[dict], dict]


def decide(signal: Signal, market_price: float, liquidity: float, spread_bps: float | None, config: dict) -> TradeDecision:
    """Turn a signal into a sized, risk-gated decision."""
    edge_floor = config["edge_floor"]
    bankroll = config["bankroll_usdc"]
    kelly_mult = config["kelly_multiplier"]
    max_frac = config["max_position_fraction"]
    min_liq = config["min_liquidity_usdc"]
    max_spread = config["max_spread_bps"]

    edge = edge_for_side(signal.p_true, market_price)
    reasons: list[str] = []

    # Hard risk gates (block entries regardless of edge).
    gates: list[str] = []
    if liquidity < min_liq:
        gates.append(f"liquidity ${liquidity:,.0f} < ${min_liq:,.0f}")
    if spread_bps is not None and spread_bps > max_spread:
        gates.append(f"spread {spread_bps:.0f}bps > {max_spread:.0f}bps")

    def hold(reason: str) -> TradeDecision:
        reasons.append(reason)
        return TradeDecision("hold", signal.p_true, market_price, edge, 0.0, 0.0, reasons)

    if signal.direction == "none":
        return hold("signal: no directional lean")
    if abs(edge) < edge_floor:
        return hold(f"|edge| {abs(edge):.1%} < floor {edge_floor:.0%}")

    if edge >= edge_floor:
        if gates:
            return hold("edge present but risk gate(s): " + "; ".join(gates))
        f = min(kelly_fraction(signal.p_true, market_price) * kelly_mult, max_frac)
        size = round(f * bankroll, 2)
        reasons.append(
            f"edge +{edge:.1%}; {kelly_mult:g}x Kelly → {f:.2%} of bankroll"
        )
        if size <= 0:
            return hold("computed size rounds to $0")
        return TradeDecision("buy", signal.p_true, market_price, edge, round(f, 4), size, reasons)

    # edge <= -edge_floor: overpriced — avoid / exit (no shorting on Polymarket).
    reasons.append(f"edge {edge:.1%} ≤ -{edge_floor:.0%}: overpriced, avoid/exit")
    return TradeDecision("sell", signal.p_true, market_price, edge, 0.0, 0.0, reasons)


def _format_decision_report(d: TradeDecision) -> str:
    head = f"DECISION: {d.action.upper()}  (p_true {d.p_true:.2f} vs price {d.market_price:.2f}, edge {d.edge:+.1%})"
    if d.action == "buy":
        head += f"  size ${d.size_usdc:,.2f} ({d.kelly_fraction:.2%} bankroll)"
    return head + "\n- " + "\n- ".join(d.reasons)


def create_decision_agent(config: dict) -> Node:
    def node(state: dict) -> dict[str, Any]:
        signal: Signal = state["signal"]
        raw = state.get("raw", {})
        ob = raw.get("orderbook", {}) or {}
        spread_bps = ob.get("spread_bps")
        liquidity = float(state.get("liquidity", 0.0) or 0.0)
        price, price_source = effective_market_price(state)
        decision = decide(signal, price, liquidity, spread_bps, config)
        decision.reasons.insert(0, f"price {price:.3f} ({price_source})")
        return {"trade_decision": decision, "decision_report": _format_decision_report(decision)}

    return node
