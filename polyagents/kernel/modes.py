"""Modes = capability subsets over the ONE kernel loop.

"One mode ↔ one agent-loop form" (per the mentor) is realised as a single
:class:`~polyagents.kernel.core.AgentLoop` fed a different capability registry per
mode — not a different engine. ReAct (``langgraph_answer``) and the Strategy
supervisor (``strategy``) are just capabilities in these registries, so Ask /
Strategy / Research all converge on the same loop.
"""
from __future__ import annotations

MODES = ("ask", "research", "lab", "strategy")


def registry_for(mode: str) -> list:
    """The capability set for a mode (real wiring; lazily built)."""
    from .wiring import default_registry

    by_name = {c.name: c for c in default_registry()}
    if mode == "ask":
        return [by_name["langgraph_answer"]]                    # ReAct as a capability
    if mode in ("research", "lab"):
        return [by_name["data_agent"], by_name["backtest_agent"]]
    if mode == "strategy":
        return [by_name["strategy"]]                            # Supervisor as a capability
    return list(by_name.values())
