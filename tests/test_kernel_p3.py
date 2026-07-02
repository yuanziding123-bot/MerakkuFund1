"""Kernel P3 — Ask / Strategy / Research all converge on the one kernel loop,
with ReAct and the Supervisor invoked as capabilities."""
from __future__ import annotations

from polyagents.kernel import run_mode
from polyagents.kernel.capabilities import (answer_capability, build_registry,
                                            strategy_capability)


def test_ask_entry_runs_through_kernel_to_react():
    reg = [answer_capability(lambda q: f"→{q}")]
    ctx = run_mode("ask", request="解释什么是校准", registry=reg)
    assert [s.capability for s in ctx.trace] == ["langgraph_answer"]   # ReAct as a capability
    assert ctx.done() and ctx.facts["answer"] == "→解释什么是校准"


def test_strategy_entry_runs_through_kernel_to_supervisor():
    reg = [strategy_capability(lambda m: {"action": "buy", "size": 100})]
    ctx = run_mode("strategy", registry=reg, market="MKT-1")
    assert [s.capability for s in ctx.trace] == ["strategy"]           # Supervisor as a capability
    assert ctx.done() and ctx.facts["decision"]["action"] == "buy"


def test_research_entry_chains_data_then_backtest():
    reg = build_registry(fetch_fn=lambda e: {"event": e, "markets": []},
                         backtest_fn=lambda h: {"n_markets": 0, "verdict": "none"})
    ctx = run_mode("research", request="backtest sports world cup", registry=reg, event="sports")
    assert [s.capability for s in ctx.trace] == ["data_agent", "backtest_agent"]
    assert ctx.done() and "backtest_report" in ctx.facts


def test_same_loop_different_registry_is_the_mode():
    # the point: one AgentLoop, the registry is the only thing that differs
    ask = run_mode("ask", request="hi", registry=[answer_capability(lambda q: "a")])
    strat = run_mode("strategy", registry=[strategy_capability(lambda m: "d")], market="M")
    assert ask.goal.label == "ask" and strat.goal.label == "strategy"
    assert ask.facts["answer"] == "a" and strat.facts["decision"] == "d"
