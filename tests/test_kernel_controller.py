"""Step 1 — the LLM controller decides, each step, how to answer: answer directly,
call one sub-agent, or chain several. Driven by a scripted fake LLM (no network)."""
from __future__ import annotations

import json

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import (answer_capability, backtest_capability,
                                            data_capability)


class FakeLLM:
    """Returns the next scripted reply per .invoke(); records the prompts it saw."""
    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[-1][1])
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def _registry():
    return [
        data_capability(lambda e: {"event": e, "markets": [1, 2, 3]}),
        backtest_capability(lambda h: {"n_markets": len(h["markets"]), "verdict": "none"}),
        answer_capability(lambda q: f"general:{q}"),
    ]


def test_answers_directly_without_calling_anything():
    llm = FakeLLM('{"action":"final","answer":"校准=预测概率与实际频率一致"}')
    res = KernelController(_registry(), llm).run("什么是校准")
    assert res.answer.startswith("校准=")
    assert res.trace == []                       # no sub-agent was needed
    assert res.steps == 0


def test_calls_one_subagent_then_answers():
    llm = FakeLLM('{"action":"call","capability":"langgraph_answer"}',
                  '{"action":"final","answer":"see above"}')
    res = KernelController(_registry(), llm).run("英伟达今天涨了吗")
    assert [s.capability for s in res.trace] == ["langgraph_answer"]
    assert res.facts["answer"] == "see above"


def test_chains_data_then_backtest_then_answers():
    llm = FakeLLM('{"action":"call","capability":"data_agent"}',
                  '{"action":"call","capability":"backtest_agent"}',
                  '{"action":"final","answer":"回测完成:3 个市场"}')
    res = KernelController(_registry(), llm).run("对世界杯做回测", event="world cup")
    assert [s.capability for s in res.trace] == ["data_agent", "backtest_agent"]
    assert res.facts["backtest_report"]["n_markets"] == 3
    assert "回测完成" in res.answer


def test_backtest_only_offered_after_data_ran():
    # backtest needs `history`; it must NOT be in the menu until data_agent runs
    llm = FakeLLM('{"action":"call","capability":"data_agent"}',
                  '{"action":"final","answer":"ok"}')
    KernelController(_registry(), llm).run("回测", event="e")
    first_menu = llm.prompts[0]
    assert "data_agent" in first_menu and "backtest_agent" not in first_menu
    assert "backtest_agent" in llm.prompts[1]     # appears only after history exists


def test_bad_capability_name_is_recovered_not_crashed():
    llm = FakeLLM('{"action":"call","capability":"does_not_exist"}',
                  '{"action":"final","answer":"recovered"}')
    res = KernelController(_registry(), llm).run("q")
    assert res.answer == "recovered" and res.trace == []


def test_budget_exhausted_forces_a_final_answer():
    # model keeps calling; controller must stop at max_steps and force an answer
    llm = FakeLLM(*(['{"action":"call","capability":"langgraph_answer"}'] * 3
                    + ['forced final text']))
    res = KernelController(_registry(), llm, max_steps=3).run("loop forever")
    assert res.steps == 3
    assert res.answer == "forced final text"      # from the forced-answer call
