"""Kernel P2 — LLM planner fallback + LangGraph/Strategy wrapped as capabilities."""
from __future__ import annotations

from types import SimpleNamespace

from polyagents.kernel.capabilities import answer_capability, strategy_capability
from polyagents.kernel.core import AgentLoop, Capability, Context, Goal
from polyagents.kernel.llm_planner import make_llm_planner


def _cap(name, pre, eff, cost=1):
    return Capability(name, name, frozenset(pre), frozenset(eff),
                      lambda ctx, eff=eff: {k: True for k in eff}, cost)


def _fake_llm(next_name):
    return SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=f'{{"next": "{next_name}"}}'))


# ----- LLM planner ----------------------------------------------------------

def test_llm_planner_picks_a_runnable_capability():
    reg = [_cap("data_agent", {"event"}, {"history"}),
           _cap("backtest_agent", {"history"}, {"backtest_report"})]
    ctx = Context(Goal(frozenset({"backtest_report"}), {"event": "X", "history": True}))
    plan = make_llm_planner(_fake_llm("backtest_agent"))
    assert plan(ctx, reg).name == "backtest_agent"


def test_llm_planner_stop_and_unknown_return_none():
    reg = [_cap("data_agent", {"event"}, {"history"})]
    ctx = Context(Goal(frozenset({"history"}), {"event": "X"}))
    assert make_llm_planner(_fake_llm("stop"))(ctx, reg) is None
    assert make_llm_planner(_fake_llm("ghost"))(ctx, reg) is None      # not in menu


def test_loop_uses_llm_fallback_when_deterministic_is_stuck():
    # goal target 'z' has no producer -> deterministic returns None; llm picks C
    reg = [_cap("c", {"x"}, {"y"})]
    loop = AgentLoop(reg, fallback_planner=make_llm_planner(_fake_llm("c")))
    ctx = loop.run(Goal(frozenset({"z"}), {"x": 1}))
    assert [s.capability for s in ctx.trace] == ["c"]     # ran via the LLM fallback
    assert not ctx.done()                                  # z still can't be produced


# ----- LangGraph / Strategy as capabilities ---------------------------------

def test_langgraph_answer_capability_runs_in_the_loop():
    cap = answer_capability(lambda q: f"answer to: {q}")
    ctx = AgentLoop([cap]).run(Goal(frozenset({"answer"}), {"question": "什么是校准"}))
    assert ctx.done() and ctx.facts["answer"] == "answer to: 什么是校准"
    assert [s.capability for s in ctx.trace] == ["langgraph_answer"]


def test_strategy_capability_runs_in_the_loop():
    cap = strategy_capability(lambda m: {"action": "buy", "size": 100})
    ctx = AgentLoop([cap]).run(Goal(frozenset({"decision"}), {"market": "MKT-1"}))
    assert ctx.done() and ctx.facts["decision"]["action"] == "buy"
