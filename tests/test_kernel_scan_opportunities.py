"""scan_opportunities — Ask-side wrapper over the Lab opportunity monitor (core,
always-on). Scores live markets with a Lab strategy and ranks dry-run trades.
Driven by a scripted fake LLM; the monitor itself is faked (no network)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import scan_opportunities_capability
from polyagents.kernel.packs import CORE, kernel_capability_names


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_scan_opportunities_is_core_always_on():
    assert "scan_opportunities" in CORE                          # always loaded, not a pack
    assert "scan_opportunities" in kernel_capability_names([])   # present with no packs selected


def test_scan_opportunities_ranks_actionable_trades():
    def fn(query):
        return {"query": query, "strategy_id": "momentum-v1", "dry_run": True, "n": 2,
                "message": "ok", "errors": [],
                "opportunities": [
                    {"question": "Spain win WC?", "action": "buy", "edge": 0.14,
                     "size_usdc": 22.0, "p_cal": 0.33, "market_price": 0.19, "apy": 1.2,
                     "reasons": ["momentum up", "flow bullish"]},
                    {"question": "Dota upset?", "action": "sell", "edge": -0.11,
                     "size_usdc": 0.0, "p_cal": 0.14, "market_price": 0.25, "apy": -0.4,
                     "reasons": []},
                ]}

    llm = FakeLLM('{"action":"call","capability":"scan_opportunities"}',
                  '{"action":"final","answer":"扫到 2 个机会"}')
    res = KernelController([scan_opportunities_capability(fn)], llm).run("现在有什么值得买的")
    assert [s.capability for s in res.trace] == ["scan_opportunities"]
    board = res.facts["opportunities"]
    assert board["strategy_id"] == "momentum-v1" and board["dry_run"] is True
    assert board["opportunities"][0]["action"] == "buy"
    assert board["opportunities"][0]["size_usdc"] == 22.0


def test_scan_opportunities_degrades_on_error():
    def fn(query):
        return {"query": query, "n": 0, "opportunities": [], "error": "RuntimeError: boom"}

    llm = FakeLLM('{"action":"call","capability":"scan_opportunities"}',
                  '{"action":"final","answer":"扫描失败"}')
    res = KernelController([scan_opportunities_capability(fn)], llm).run("扫机会")
    assert res.facts["opportunities"]["error"].startswith("RuntimeError")
