"""alpha-research pack — relational_alpha (event-relatedness engine) + research_alpha
(strategy validation + improvement). Selectable, not core. Workers faked (no network)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import (relational_alpha_capability,
                                            research_alpha_capability)
from polyagents.kernel.packs import CORE, PACKS, kernel_capability_names


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_alpha_research_pack_is_selectable_not_core():
    assert "alpha-research" in PACKS
    assert PACKS["alpha-research"]["capabilities"] == ["relational_alpha", "research_alpha"]
    for cap in ("relational_alpha", "research_alpha"):
        assert cap not in CORE
        assert cap not in kernel_capability_names([])
        assert cap in kernel_capability_names(["alpha-research"])


def test_relational_alpha_surfaces_lag_and_whatif():
    def fn(query):
        return {"query": query, "event": "the 2026 fifa world cup", "n_field": 6,
                "field_sum": 0.98, "consistency": "tight", "signal": "buy",
                "target": {"question": "Will France win?", "price": 0.38, "fair_share": 0.39},
                "target_recent_delta": 0.0, "field_released": 0.06,
                "implied_target_rise": 0.03, "lag_gap": 0.03,
                "top_rivals": [{"question": "Will Brazil win?", "price": 0.2, "delta": -0.06}],
                "what_if": [{"question": "Will Brazil win?", "target_fair_if_out": 0.47, "delta": 0.09}]}

    llm = FakeLLM('{"action":"call","capability":"relational_alpha"}',
                  '{"action":"final","answer":"法国相对被低估"}')
    res = KernelController([relational_alpha_capability(fn)], llm).run("法国夺冠有没有 alpha")
    board = res.facts["relational_alpha"]
    assert board["lag_gap"] == 0.03 and board["signal"] == "buy"
    assert board["what_if"][0]["delta"] == 0.09


def test_research_alpha_returns_review_over_evidence():
    def fn(query):
        return {"query": query, "news_signal": "bullish",
                "relational": {"field_sum": 0.98, "lag_gap": 0.03, "signal": "buy"},
                "review": "1) 复述 2) 有弱 alpha 3) 加点差过滤"}

    llm = FakeLLM('{"action":"call","capability":"research_alpha"}',
                  '{"action":"final","answer":"已评审"}')
    res = KernelController([research_alpha_capability(fn)], llm).run("验证我的策略：对手爆冷就买法国")
    r = res.facts["alpha_review"]
    assert "改进" in r["review"] or "alpha" in r["review"]
    assert r["relational"]["signal"] == "buy"
