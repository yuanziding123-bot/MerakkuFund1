"""Modes = capability subsets over the ONE kernel loop.

"One mode ↔ one agent-loop form" (per the mentor) is realised as a single
:class:`~polyagents.kernel.core.AgentLoop` fed a different capability registry per
mode — not a different engine. ReAct (``langgraph_answer``) and the Strategy
supervisor (``strategy``) are just capabilities in these registries, so Ask /
Strategy / Research all converge on the same loop.
"""
from __future__ import annotations

MODES = ("ask", "research", "lab", "strategy")


def registry_for(mode: str, packs: list[str] | None = None) -> list:
    """The capability set for a mode (real wiring; lazily built).

    For ``kernel`` the registry is CORE + the selected vertical ``packs`` (``None`` =
    all packs, backward-compatible; ``[]`` = core only) — the on-demand vertical load
    the mentor's model calls for. Other modes keep their fixed subset."""
    from .packs import kernel_capability_names
    from .wiring import default_registry

    by_name = {c.name: c for c in default_registry()}
    if mode == "ask":
        return [by_name["langgraph_answer"]]                    # ReAct as a capability
    if mode in ("research", "lab"):
        return [by_name["data_agent"], by_name["backtest_agent"]]
    if mode == "strategy":
        return [by_name["strategy"]]                            # Supervisor as a capability
    # kernel: core + selected vertical packs
    return [by_name[n] for n in kernel_capability_names(packs) if n in by_name]
