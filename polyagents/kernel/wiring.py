"""Real wiring for the kernel — a registry backed by the live engine, Lab
BacktestRunner, the Ask LangGraph agent, and the Strategy supervisor.

Lazy + best-effort (imports and the engine are only touched when built), so the
kernel core stays import-light and offline-testable (tests inject fakes; this is
the production wiring). Needs network / ANTHROPIC_API_KEY at run time.
"""
from __future__ import annotations

from .capabilities import (answer_capability, backtest_capability, data_capability,
                           domain_capability, strategy_capability)


def default_registry() -> list:
    """Wire data → backtest (over resolved markets), plus answer + strategy.

    ``event`` is treated as a free-text handle; we slice resolved markets by its
    keyword category, then replay a deterministic backtest over them."""
    from polyagents import mcp_server
    from polyagents.evaluation.evaluate import categorize
    from polyagents.lab.backtest import BacktestRunner

    eng = mcp_server.engine()

    def fetch(event):
        cat = categorize(event or "")
        raw = eng.client.list_resolved_markets(limit=80)
        yes = [m for m in eng.client.to_markets(raw) if m.outcome == "YES"]
        if cat != "other":
            yes = [m for m in yes if categorize(m.question) == cat]
        return {"event": event, "category": cat, "markets": yes}

    def backtest(history):
        out = BacktestRunner(client=eng.client, max_markets=20).replay(
            category=None, markets=history["markets"])
        s = out["summary"]
        return {"event": history.get("event"), "n_markets": out["n_markets"],
                "brier_delta": s.brier_delta, "beats_market": s.beats_market,
                "ci": list(s.brier_delta_ci)}

    def _last_content(res):
        msgs = res.get("messages", []) if isinstance(res, dict) else []
        last = msgs[-1] if msgs else None
        return getattr(last, "content", "") if last is not None else ""

    def answer(question):                              # general / web-search agent
        from polyagents.web.agent import build_general_agent
        return _last_content(build_general_agent().invoke(
            {"messages": [("user", question or "")]}))

    def domain_answer(question):                       # read-only market-tools agent
        from polyagents.web.agent import build_agent
        return _last_content(build_agent(readonly=True).invoke(
            {"messages": [("user", question or "")]}))

    def run_strategy(market):
        from polyagents.orchestration import run_strategy as _rs
        bb = _rs(market, graph=eng, config=eng.config, strategy="full")
        return bb.risk

    return [
        data_capability(fetch),
        backtest_capability(backtest),
        answer_capability(answer),
        domain_capability(domain_answer),
        strategy_capability(run_strategy),
    ]
