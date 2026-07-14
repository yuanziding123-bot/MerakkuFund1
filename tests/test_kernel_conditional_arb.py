"""conditional-arb pack — scan_conditional_arb (cross-market conditional / logical-
implication arbitrage). Selectable, not core. Worker faked (no network)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import scan_conditional_arb_capability
from polyagents.kernel.packs import CORE, PACKS, kernel_capability_names
from polyagents.web import server


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_conditional_arb_pack_selectable():
    assert "conditional-arb" in PACKS
    assert PACKS["conditional-arb"]["capabilities"] == ["scan_conditional_arb"]
    assert "scan_conditional_arb" not in CORE
    assert "scan_conditional_arb" not in kernel_capability_names([])
    assert "scan_conditional_arb" in kernel_capability_names(["conditional-arb"])


def test_scan_conditional_arb_routes_and_captures():
    def fn(query):
        return {"query": query, "n_entities": 3, "n_chains": 1, "n_true_arb": 0,
                "chains": [{"entity": "france", "p_champ": 0.39, "p_advance": 0.60,
                            "cond_champ_given_advance": 0.65, "has_arb": False, "violations": [],
                            "chain": [{"level": 4, "question": "Will France win?", "price": 0.39},
                                      {"level": 3, "question": "Will France reach final?", "price": 0.60}]}]}

    llm = FakeLLM('{"action":"call","capability":"scan_conditional_arb"}',
                  '{"action":"final","answer":"扫到1条条件链"}')
    res = KernelController([scan_conditional_arb_capability(fn)], llm).run("找条件概率套利")
    assert [s.capability for s in res.trace] == ["scan_conditional_arb"]
    assert res.facts["conditional_arb"]["chains"][0]["cond_champ_given_advance"] == 0.65


def test_render_flags_true_implication_arb():
    a = {"n_entities": 2, "n_chains": 1, "n_true_arb": 1,
         "chains": [{"entity": "x", "p_champ": 0.50, "p_advance": 0.40,
                     "cond_champ_given_advance": 1.25, "has_arb": True,
                     "violations": [{"stronger": "X win", "p_strong": 0.50,
                                     "weaker": "X advance", "p_weak": 0.40, "gap": 0.10}],
                     "chain": []}]}
    md = server._format_conditional_arb(a, "p")
    assert "无风险套利" in md and "gap" in md            # true arb surfaced
    # honesty note that the chained P(match)*P(champ) is NOT a valid arb
    assert "不可执行" in md or "不是有效套利" in md
