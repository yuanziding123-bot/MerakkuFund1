"""Signal agent — LLM probability read from Layer 1 data.

Synthesises the data-collection reports + factor vector into a structured
:class:`Signal` (estimated true probability, direction, conviction, rationale).
Philosophy mirrors the polymarket reference repo: *track the money, don't
predict the event* — order-book pressure, trade flow and sentiment are the
evidence, not a personal forecast.

The ``llm`` is injected and only needs ``.with_structured_output(Signal)``, so
tests pass a fake and never hit the network.
"""
from __future__ import annotations

from typing import Any, Callable

from .schemas import Signal

Node = Callable[[dict], dict]

_SYSTEM = """You are a Polymarket signal analyst. Estimate the TRUE probability that the \
analysed outcome side resolves YES (pays $1), using the evidence below. Weigh \
"smart money" microstructure and trade flow heavily; treat news/sentiment as \
context. Do not invent facts. Track the money rather than forecasting the event \
from priors. Return your estimate as structured output."""


def _build_prompt(state: dict) -> str:
    raw = state.get("raw", {})
    factors = (raw.get("features", {}) or {}).get("factors", {})
    return (
        f"{_SYSTEM}\n\n"
        f"=== Market ===\n{state.get('market_context', '')}\n\n"
        f"=== Price ===\n{state.get('price_report', '')}\n"
        f"=== Volume ===\n{state.get('volume_report', '')}\n"
        f"=== Order book ===\n{state.get('orderbook_report', '')}\n"
        f"=== Trade flow ===\n{state.get('trades_flow_report', '')}\n"
        f"=== News ===\n{state.get('news_report', '')}\n\n"
        f"=== Factor vector ===\n{factors}\n\n"
        f"Current market price for the analysed side: {state.get('market_price')}.\n"
        f"Give p_true (0-1), a direction, conviction, and a short rationale."
    )


def create_signal_agent(llm) -> Node:
    structured = llm.with_structured_output(Signal)

    def node(state: dict) -> dict[str, Any]:
        signal: Signal = structured.invoke(_build_prompt(state))
        report = (
            f"SIGNAL: {signal.direction.upper()} p_true={signal.p_true:.2f} "
            f"({signal.conviction})\n{signal.rationale}"
        )
        return {"signal": signal, "signal_report": report}

    return node
