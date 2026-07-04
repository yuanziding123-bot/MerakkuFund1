"""settle_and_reflect — settle resolved paper trades + Layer-4 reflection (paper-exec pack)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import settle_and_reflect_capability
from polyagents.kernel.packs import PACKS, kernel_capability_names


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_settle_in_paper_exec_pack():
    assert "settle_and_reflect" in PACKS["paper-exec"]["capabilities"]
    assert "settle_and_reflect" not in kernel_capability_names([])            # gated
    assert "settle_and_reflect" in kernel_capability_names(["paper-exec"])    # loads with the pack


def test_settle_returns_records_and_lessons():
    def fn(q):
        return {"n_settled": 1, "settled": [
            {"question": "X?", "won": True, "realized_pnl": 25.0, "realized_return": 0.5,
             "lesson": "flow signal was right; size up next time"}],
            "portfolio": {"cash": 525.0, "realized_pnl": 25.0, "open_positions": []}}
    llm = FakeLLM('{"action":"call","capability":"settle_and_reflect"}',
                  '{"action":"final","answer":"结算 1 笔,赢"}')
    res = KernelController([settle_and_reflect_capability(fn)], llm).run("结算我的交易")
    s = res.facts["settlement"]
    assert s["n_settled"] == 1
    assert s["settled"][0]["lesson"].startswith("flow signal")
