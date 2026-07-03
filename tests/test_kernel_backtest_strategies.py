"""Multi-strategy backtest — 'compare the strategies for a domain' runs the loop and
produces a per-strategy comparison. Driven by a scripted fake LLM; capability uses an
injected fake backtester."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import backtest_strategies_capability


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages[-1][1])
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def _registry(captured):
    def fn(query):
        captured["query"] = query
        return {"domain": "sports", "n_markets": 20, "strategies": [
            {"name": "naive", "brier_delta": 0.0, "beats_market": False, "ci": [-0.001, 0.001]},
            {"name": "momentum", "brier_delta": -0.0009, "beats_market": False, "ci": [-0.0017, -0.0002]}],
            "best": {"name": "naive", "brier_delta": 0.0, "beats_market": False}}
    return [backtest_strategies_capability(fn)]


def test_compare_strategies_over_a_domain():
    captured = {}
    llm = FakeLLM('{"action":"call","capability":"backtest_strategies"}',
                  '{"action":"final","answer":"naive 最优,但都没跑赢市场"}')
    res = KernelController(_registry(captured), llm).run("在体育领域对比回测这些策略,看哪个跑赢市场")
    assert [s.capability for s in res.trace] == ["backtest_strategies"]
    c = res.facts["strategy_comparison"]
    assert {s["name"] for s in c["strategies"]} == {"naive", "momentum"}
    assert c["best"]["name"] == "naive"                 # highest brier_delta ranked first
    assert c["n_markets"] == 20
