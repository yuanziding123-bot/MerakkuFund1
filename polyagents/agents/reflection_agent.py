"""Reflection agent — LLM self-critique of the decision.

TradingAgents runs a reflection step that turns realised P&L into lessons. We
have no realised return yet (that's Layer 4), so this is a *pre-trade*
self-critique: given the signal and the sized decision, is it sound? What could
be wrong (shaky assumptions, out-of-distribution market, thin data)? It writes a
structured :class:`Reflection` the decision/audit log can carry forward.
"""
from __future__ import annotations

from typing import Any, Callable

from .schemas import Reflection, Signal, TradeDecision

Node = Callable[[dict], dict]

_SYSTEM = """You are a risk-minded reviewer. Critique the proposed Polymarket trade \
decision against the evidence. Be skeptical: surface shaky assumptions, thin or \
stale data, out-of-distribution conditions, and microstructure/liquidity risks. \
You are reviewing the decision quality, not re-deriving it. Return structured \
output."""


def _build_prompt(state: dict) -> str:
    signal: Signal = state["signal"]
    decision: TradeDecision = state["trade_decision"]
    return (
        f"{_SYSTEM}\n\n"
        f"=== Market ===\n{state.get('market_context', '')}\n\n"
        f"=== Signal ===\n{state.get('signal_report', '')}\n\n"
        f"=== Decision ===\n{state.get('decision_report', '')}\n\n"
        f"Signal conviction: {signal.conviction}. Action: {decision.action}, "
        f"edge {decision.edge:+.1%}, size ${decision.size_usdc:,.2f}.\n"
        f"Assess soundness, list concrete risk flags, and give your confidence."
    )


def create_reflection_agent(llm) -> Node:
    structured = llm.with_structured_output(Reflection)

    def node(state: dict) -> dict[str, Any]:
        reflection: Reflection = structured.invoke(_build_prompt(state))
        flags = "\n".join(f"- {f}" for f in reflection.risk_flags) or "- (none)"
        report = (
            f"REFLECTION ({reflection.confidence} confidence): {reflection.assessment}\n"
            f"Risk flags:\n{flags}"
        )
        return {"reflection": reflection, "reflection_report": report}

    return node
