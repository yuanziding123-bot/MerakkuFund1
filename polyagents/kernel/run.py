"""Unified entry — every mode runs through the ONE kernel.

``run_mode(mode, …)`` is the convergence point. For the open-ended **kernel** mode
the :class:`~polyagents.kernel.controller.KernelController` (LLM-driven) is the
primary driver — it decides, each step, how to answer and which sub-agents to
call. The other modes (ask/strategy/research) still run the deterministic
goal-directed :class:`AgentLoop`; ReAct and the Strategy supervisor are invoked as
capabilities. When no LLM is available, kernel mode falls back to the deterministic
loop so it stays offline-runnable.
"""
from __future__ import annotations

import os

from .controller import KernelController, KernelResult
from .core import AgentLoop, Context, Goal
from .intent import recognize
from .modes import registry_for


def _goal_for(mode: str, request: str | None, facts: dict) -> Goal:
    if mode == "strategy":
        return Goal(frozenset({"decision"}), {"market": facts.get("market")}, "strategy")
    if mode in ("research", "lab"):
        return Goal(frozenset({"backtest_report"}),
                    {"event": facts.get("event") or request}, "backtest")
    if mode == "ask":
        return recognize(request or "", event=facts.get("event"))
    return recognize(request or "")


def _default_controller_llm():
    """Build the controller LLM (best-effort; None if unavailable → deterministic)."""
    try:
        from langchain_anthropic import ChatAnthropic
        from polyagents.web.agent import resolve_model
        model = os.getenv("KERNEL_CONTROLLER_MODEL") or resolve_model(None)
        return ChatAnthropic(model=model, temperature=0.0)
    except Exception:
        return None


def _as_context(result: KernelResult) -> Context:
    """Adapt a controller result to a Context (so web/_kernel_summary keep working)."""
    ctx = Context(Goal(frozenset({"answer"}), {}, "kernel"))
    ctx.facts = result.facts
    ctx.trace = result.trace
    return ctx


def run_mode(mode: str, *, request: str | None = None, registry: list | None = None,
             max_steps: int = 12, llm=None, fallback_planner=None, audit=None,
             on_event=None, **facts) -> Context:
    """Run ``mode`` through the kernel. ``registry`` overrides the wiring (tests);
    ``llm`` overrides the controller model (kernel mode). Returns a Context."""
    reg = registry if registry is not None else registry_for(mode)
    if mode == "kernel":
        controller_llm = llm if llm is not None else _default_controller_llm()
        if controller_llm is not None:
            facts.setdefault("event", request)     # so data_agent is selectable by the LLM
            ctrl = KernelController(reg, controller_llm, max_steps=max_steps,
                                    on_event=on_event, audit=audit)
            return _as_context(ctrl.run(request or "", **facts))
        # no LLM → deterministic fallback below (offline-runnable)
    goal = _goal_for(mode, request, facts)
    loop = AgentLoop(reg, max_steps=max_steps, fallback_planner=fallback_planner,
                     audit=audit, on_event=on_event)
    return loop.run(goal)
