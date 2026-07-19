"""range-hedge pack — hedge_scan (measure price swing + max lockable hedge profit).
Selectable, not core. Worker faked (no network)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import hedge_scan_capability
from polyagents.kernel.packs import CORE, PACKS, kernel_capability_names
from polyagents.web import server


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_range_hedge_pack_selectable():
    assert PACKS["range-hedge"]["capabilities"] == ["hedge_scan"]
    assert "hedge_scan" not in CORE
    assert "hedge_scan" in kernel_capability_names(["range-hedge"])


def test_hedge_scan_routes_and_renders():
    def fn(query):
        return {"query": query, "market": "F1 Belgian Grand Prix: Safety Car?",
                "matched_by": "search(5)", "n": 36, "current": 0.775, "low": 0.57,
                "high": 0.84, "range": 0.27, "lockable": 0.27, "verdict": "波动大,适合区间对冲锁利"}

    llm = FakeLLM('{"action":"call","capability":"hedge_scan"}',
                  '{"action":"final","answer":"摆幅0.27,可锁利0.27"}')
    res = KernelController([hedge_scan_capability(fn)], llm).run("比利时安全车能不能对冲锁利")
    r = res.facts["hedge_scan"]
    assert r["lockable"] == 0.27
    md = server._format_hedge(r, "hedge_scan")
    assert "可锁定利润 = 0.27" in md and "Safety Car" in md and "YES" in md
