"""paper_trade — the loop's gated 'act' capability (pack: paper-exec)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import paper_trade_capability
from polyagents.kernel.packs import PACKS, kernel_capability_names


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_paper_trade_is_a_gated_pack():
    assert "paper_trade" in PACKS["paper-exec"]["capabilities"]
    assert "paper_trade" not in kernel_capability_names([])            # not core
    assert "paper_trade" in kernel_capability_names(["paper-exec"])    # loads when selected


def test_paper_trade_needs_market_ref_then_executes():
    captured = {}

    def fn(ref):
        captured["ref"] = ref
        return {"market": {"question": "X?", "price": 0.5}, "action": "buy", "p_true": 0.6,
                "edge": 0.09, "size_usdc": 50.0, "executed": True,
                "result": {"status": "filled", "realized_pnl": 0.0},
                "portfolio": {"cash": 450.0, "exposure": 50.0, "realized_pnl": 0.0,
                              "open_positions": [{"market": "X?"}]}}

    # market_ref must be on the board first (precond) — resolve, then paper_trade
    def resolve(_ctx):
        return {"market_ref": {"token_id": "t1", "question": "X?"}}
    from polyagents.kernel.capabilities import resolve_market_capability
    reg = [resolve_market_capability(lambda q: {"token_id": "t1", "question": "X?"}),
           paper_trade_capability(fn)]
    llm = FakeLLM('{"action":"call","capability":"resolve_market"}',
                  '{"action":"call","capability":"paper_trade"}',
                  '{"action":"final","answer":"已下纸面单"}')
    res = KernelController(reg, llm).run("paper trade X")
    assert [s.capability for s in res.trace] == ["resolve_market", "paper_trade"]
    assert res.facts["paper_trade"]["executed"] is True
    assert captured["ref"]["token_id"] == "t1"
