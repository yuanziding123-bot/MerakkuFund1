"""Layer 2 — the decision engine (signal / decision / reflection agents).

Mirrors TradingAgents' agent-node factory pattern, simplified to the Merakku
v3.0 three-agent architecture: a Signal agent (LLM) estimates true probability
from Layer 1 data, a Decision agent (deterministic risk + Kelly sizing) turns
that into a trade, and a Reflection agent (LLM) self-critiques the decision.
"""
from __future__ import annotations

from .decision_agent import create_decision_agent, decide
from .reflection_agent import create_reflection_agent
from .risk import edge_for_side, kelly_fraction
from .schemas import Reflection, Signal, TradeDecision
from .signal_agent import create_signal_agent

__all__ = [
    "Signal",
    "Reflection",
    "TradeDecision",
    "create_signal_agent",
    "create_decision_agent",
    "create_reflection_agent",
    "decide",
    "edge_for_side",
    "kelly_fraction",
]
