"""Tests for the LLM agents (signal, reflection) using a fake LLM."""
from __future__ import annotations

from polyagents.agents.decision_agent import create_decision_agent
from polyagents.agents.reflection_agent import create_reflection_agent
from polyagents.agents.signal_agent import create_signal_agent
from polyagents.agents.schemas import Signal
from polyagents.default_config import DEFAULT_CONFIG


def _state():
    return {
        "market_context": "Market: will it rain? side YES",
        "market_price": 0.50,
        "liquidity": 20000.0,
        "price_report": "p", "volume_report": "v", "orderbook_report": "ob",
        "trades_flow_report": "tf", "news_report": "n", "features_report": "f",
        "raw": {"features": {"factors": {"flow_imbalance": 0.2}},
                "orderbook": {"spread_bps": 120.0}},
    }


def test_signal_agent_writes_structured_signal(fake_llm):
    node = create_signal_agent(fake_llm)
    out = node(_state())
    assert isinstance(out["signal"], Signal)
    assert out["signal"].p_true == 0.70
    assert "SIGNAL" in out["signal_report"]


def test_decision_agent_consumes_signal(fake_llm):
    state = _state()
    state["signal"] = Signal(direction="yes", p_true=0.70, conviction="high", rationale="r")
    node = create_decision_agent(DEFAULT_CONFIG.copy())
    out = node(state)
    assert out["trade_decision"].action == "buy"
    assert "DECISION" in out["decision_report"]


def test_reflection_agent_writes_structured_reflection(fake_llm):
    from polyagents.agents.schemas import Reflection, TradeDecision

    state = _state()
    state["signal"] = Signal(direction="yes", p_true=0.70, conviction="high", rationale="r")
    state["signal_report"] = "SIGNAL: YES"
    state["trade_decision"] = TradeDecision("buy", 0.70, 0.50, 0.20, 0.05, 25.0, ["edge"])
    state["decision_report"] = "DECISION: BUY"
    node = create_reflection_agent(fake_llm)
    out = node(state)
    assert isinstance(out["reflection"], Reflection)
    assert "REFLECTION" in out["reflection_report"]
    assert out["reflection"].risk_flags  # non-empty
