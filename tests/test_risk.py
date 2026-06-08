"""Tests for the deterministic decision agent (edge / Kelly / risk gates)."""
from __future__ import annotations

from pytest import approx

from polyagents.agents.decision_agent import decide
from polyagents.agents.risk import edge_for_side, effective_market_price, kelly_fraction
from polyagents.agents.schemas import Signal
from polyagents.default_config import DEFAULT_CONFIG


def test_effective_price_prefers_live_book_mid():
    # live book mid present -> use it (not the stale snapshot)
    state = {"market_price": 0.69, "raw": {"orderbook": {"mid": 0.735}}}
    price, source = effective_market_price(state)
    assert price == 0.735 and source == "live book mid"
    # no book -> fall back to the snapshot price
    price, source = effective_market_price({"market_price": 0.69, "raw": {}})
    assert price == 0.69 and source == "market snapshot"


def _cfg():
    return DEFAULT_CONFIG.copy()


def _sig(direction="yes", p_true=0.70):
    return Signal(direction=direction, p_true=p_true, conviction="high", rationale="r")


def test_edge_and_kelly_math():
    assert edge_for_side(0.70, 0.50) == approx(0.20)
    assert kelly_fraction(0.70, 0.50) == approx(0.40)  # (0.7-0.5)/(1-0.5)
    assert kelly_fraction(0.50, 0.50) == 0.0           # no edge
    assert kelly_fraction(0.40, 0.50) == 0.0           # negative -> clamped
    assert kelly_fraction(0.99, 1.0) == 0.0            # no room -> guarded


def test_buy_sized_by_fractional_kelly_capped():
    d = decide(_sig(p_true=0.70), market_price=0.50, liquidity=20000, spread_bps=100, config=_cfg())
    assert d.action == "buy"
    # full Kelly 0.40 * 0.25 = 0.10, capped at max_position_fraction 0.05
    assert d.kelly_fraction == 0.05
    assert d.size_usdc == 25.0                        # 0.05 * 500 bankroll


def test_hold_on_thin_edge():
    d = decide(_sig(p_true=0.52), market_price=0.50, liquidity=20000, spread_bps=100, config=_cfg())
    assert d.action == "hold"
    assert d.size_usdc == 0.0


def test_risk_gate_low_liquidity_blocks_entry():
    d = decide(_sig(p_true=0.70), market_price=0.50, liquidity=1000, spread_bps=100, config=_cfg())
    assert d.action == "hold"
    assert any("liquidity" in r for r in d.reasons)


def test_risk_gate_wide_spread_blocks_entry():
    d = decide(_sig(p_true=0.70), market_price=0.50, liquidity=20000, spread_bps=500, config=_cfg())
    assert d.action == "hold"
    assert any("spread" in r for r in d.reasons)


def test_sell_when_overpriced():
    d = decide(_sig(p_true=0.30), market_price=0.50, liquidity=20000, spread_bps=100, config=_cfg())
    assert d.action == "sell"
    assert d.edge < 0


def test_direction_none_holds():
    d = decide(_sig(direction="none", p_true=0.70), market_price=0.50, liquidity=20000, spread_bps=100, config=_cfg())
    assert d.action == "hold"
