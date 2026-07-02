"""Finance capabilities for the kernel — declared with preconditions/effects so
the planner can chain them automatically.

Each builder takes its worker by injection (``fetch_fn`` etc.), so the registry
runs offline in tests and can be wired to the real engine in prod. The capability
set passed to an :class:`~polyagents.kernel.core.AgentLoop` *is* the "mode".
"""
from __future__ import annotations

from typing import Callable

from .core import Capability, Context


def data_capability(fetch_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"history": fetch_fn(ctx.facts.get("event"))}
    return Capability("data_agent", "Fetch historical data for an event/market.",
                      frozenset({"event"}), frozenset({"history"}), run, cost=1)


def backtest_capability(backtest_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"backtest_report": backtest_fn(ctx.facts["history"])}
    return Capability("backtest_agent", "Backtest a signal over historical data → report.",
                      frozenset({"history"}), frozenset({"backtest_report"}), run, cost=2)


def signal_capability(signal_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"signal": signal_fn(ctx.facts["history"])}
    return Capability("signal_agent", "Estimate a probability signal from data.",
                      frozenset({"history"}), frozenset({"signal"}), run, cost=2)


def risk_capability(risk_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"decision": risk_fn(ctx.facts["signal"])}
    return Capability("risk_agent", "Size + risk-gate a decision from a signal.",
                      frozenset({"signal"}), frozenset({"decision"}), run, cost=2)


def answer_capability(answer_fn: Callable) -> Capability:
    """Wrap the existing LangGraph ReAct agent as ONE capability: question → answer.

    This is the point of the kernel — LangGraph becomes a capability inside the
    loop, not the top-level orchestrator. ``answer_fn(question) -> str``.
    """
    def run(ctx: Context) -> dict:
        return {"answer": answer_fn(ctx.facts.get("question", ""))}
    return Capability("langgraph_answer",
                      "General / open-ended Q&A (concepts, coding, outside info) via a "
                      "general agent with web search. NOT for our own market data.",
                      frozenset({"question"}), frozenset({"answer"}), run, cost=3)


def domain_capability(answer_fn: Callable) -> Capability:
    """Wrap the read-only market-tools ReAct agent as ONE capability: question →
    answer, using live domain tools (scan / orderbook / evaluate). Same effect as
    ``langgraph_answer`` so the controller picks by *fit* — this one when the
    question is about OUR prediction markets / data / evaluation. ``answer_fn(q)->str``.
    """
    def run(ctx: Context) -> dict:
        return {"answer": answer_fn(ctx.facts.get("question", ""))}
    return Capability("domain_answer",
                      "Q&A about OUR prediction markets / data / evaluation using live "
                      "read-only tools (scan markets, orderbook, calibration/evaluate).",
                      frozenset({"question"}), frozenset({"answer"}), run, cost=3)


def strategy_capability(run_strategy_fn: Callable) -> Capability:
    """Wrap the multi-agent Strategy supervisor as one capability: market → decision.

    ``run_strategy_fn(market) -> decision`` (the supervisor's own data→signal→risk
    loop runs inside this single capability)."""
    def run(ctx: Context) -> dict:
        return {"decision": run_strategy_fn(ctx.facts["market"])}
    return Capability("strategy", "Run the data→signal→risk Strategy supervisor.",
                      frozenset({"market"}), frozenset({"decision"}), run, cost=4)


def build_registry(*, fetch_fn: Callable, backtest_fn: Callable | None = None,
                   signal_fn: Callable | None = None,
                   risk_fn: Callable | None = None) -> list[Capability]:
    """Assemble a capability registry from injected workers (a 'mode')."""
    reg = [data_capability(fetch_fn)]
    if backtest_fn:
        reg.append(backtest_capability(backtest_fn))
    if signal_fn:
        reg.append(signal_capability(signal_fn))
    if risk_fn:
        reg.append(risk_capability(risk_fn))
    return reg


def demo_registry() -> list[Capability]:
    """A fully-offline registry (deterministic fakes) so the loop is runnable and
    demoable without network. Real wiring (engine client + Lab BacktestRunner) is
    a thin follow-up — swap the fns in :func:`build_registry`."""
    def fetch(event):
        return {"event": event, "candles": [0.40, 0.45, 0.50, 0.55, 0.60]}

    def backtest(history):
        c = history["candles"]
        return {"event": history["event"], "n": len(c),
                "trend": round(c[-1] - c[0], 3), "verdict": "demo"}

    def signal(history):
        c = history["candles"]
        return {"p_true": min(0.98, max(0.02, c[-1]))}

    def risk(signal):
        return {"action": "buy" if signal["p_true"] > 0.5 else "hold"}

    return build_registry(fetch_fn=fetch, backtest_fn=backtest,
                          signal_fn=signal, risk_fn=risk)
