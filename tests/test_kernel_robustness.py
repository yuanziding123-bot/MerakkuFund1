"""Step 6 — robustness: capability errors are recovered by re-planning, the
scratchpad is exposed, and an unusable LLM falls back to the deterministic loop."""
from __future__ import annotations

from polyagents.kernel import run_mode
from polyagents.kernel import run as run_mod
from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import (answer_capability, backtest_capability,
                                            build_registry, data_capability)


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


class DeadLLM:
    """Every call fails — simulates the disabled/offline org."""
    def invoke(self, messages):
        raise RuntimeError("This organization has been disabled.")


def _boom(_):
    raise RuntimeError("data source down")


def test_capability_error_is_recovered_by_replanning():
    reg = [data_capability(_boom), answer_capability(lambda q: "fallback answer")]
    llm = FakeLLM('{"action":"call","capability":"data_agent"}',      # fails
                  '{"action":"call","capability":"langgraph_answer"}',  # re-plan to another
                  '{"action":"final","answer":"recovered"}')
    res = KernelController(reg, llm).run("q", event="e")
    assert res.trace[0].ok is False and res.trace[0].capability == "data_agent"
    assert res.trace[1].capability == "langgraph_answer"              # switched path after failure
    assert res.answer == "recovered"
    assert any("failed" in n for n in res.notes)                     # scratchpad recorded the error


def test_scratchpad_notes_are_exposed():
    reg = [answer_capability(lambda q: "a")]
    llm = FakeLLM('{"action":"call","capability":"langgraph_answer"}',
                  '{"action":"final","answer":"ok"}')
    res = KernelController(reg, llm).run("q")
    assert any("called langgraph_answer" in n for n in res.notes)


def test_dead_llm_is_flagged_not_ok():
    reg = [answer_capability(lambda q: "a")]
    res = KernelController(reg, DeadLLM(), max_steps=2).run("q")
    assert res.llm_ok is False and res.answer == "(no answer)"


def test_run_mode_kernel_falls_back_to_deterministic_when_llm_dead(monkeypatch):
    # the real situation: org disabled → controller can't drive → deterministic path
    monkeypatch.setattr(run_mod, "_default_controller_llm", lambda *_a: DeadLLM())
    reg = build_registry(fetch_fn=lambda e: {"event": e, "markets": [1, 2]},
                         backtest_fn=lambda h: {"n_markets": len(h["markets"])})
    ctx = run_mode("kernel", request="对某事件做 backtest", registry=reg)
    assert [s.capability for s in ctx.trace] == ["data_agent", "backtest_agent"]
    assert "backtest_report" in ctx.facts                            # still works without a live LLM
