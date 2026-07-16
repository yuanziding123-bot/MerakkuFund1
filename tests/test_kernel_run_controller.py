"""Step 2 — run_mode('kernel') is driven by the LLM controller (primary), and
falls back to the deterministic loop when no LLM is available."""
from __future__ import annotations

from polyagents.kernel import run_mode
from polyagents.kernel import run as run_mod
from polyagents.kernel.capabilities import (answer_capability, backtest_capability,
                                            build_registry, data_capability)


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def _reg():
    return [
        data_capability(lambda e: {"event": e, "markets": [1, 2, 3]}),
        backtest_capability(lambda h: {"n_markets": len(h["markets"])}),
        answer_capability(lambda q: f"general:{q}"),
    ]


def test_kernel_mode_direct_answer_via_controller():
    llm = FakeLLM('{"action":"final","answer":"校准是概率与频率一致"}')
    ctx = run_mode("kernel", request="什么是校准", registry=_reg(), llm=llm)
    assert ctx.facts["answer"].startswith("校准")
    assert [s.capability for s in ctx.trace] == []          # answered without a sub-agent


def test_kernel_mode_chains_data_backtest_via_controller():
    llm = FakeLLM('{"action":"call","capability":"data_agent"}',
                  '{"action":"call","capability":"backtest_agent"}',
                  '{"action":"final","answer":"回测: 3 个市场"}')
    ctx = run_mode("kernel", request="对世界杯做回测", registry=_reg(), llm=llm)
    assert [s.capability for s in ctx.trace] == ["data_agent", "backtest_agent"]
    assert ctx.facts["backtest_report"]["n_markets"] == 3   # event auto-seeded = request
    assert "回测" in ctx.facts["answer"]


def test_kernel_mode_falls_back_to_deterministic_without_llm(monkeypatch):
    monkeypatch.setattr(run_mod, "_default_controller_llm", lambda *_a: None)
    reg = build_registry(fetch_fn=lambda e: {"event": e, "markets": [1, 2]},
                         backtest_fn=lambda h: {"n_markets": len(h["markets"])})
    ctx = run_mode("kernel", request="对某事件做 backtest", registry=reg)  # llm=None
    # deterministic recognize -> backtest goal -> data_agent -> backtest_agent
    assert [s.capability for s in ctx.trace] == ["data_agent", "backtest_agent"]
    assert "backtest_report" in ctx.facts
