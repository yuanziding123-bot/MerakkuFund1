"""market-radar pack — market_radar ('what changed today' discovery funnel).
Selectable, not core. Worker faked (no network)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import market_radar_capability
from polyagents.kernel.packs import CORE, PACKS, kernel_capability_names
from polyagents.web import server


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_market_radar_pack_selectable():
    assert "market-radar" in PACKS
    assert PACKS["market-radar"]["capabilities"] == ["market_radar"]
    assert "market_radar" not in CORE
    assert "market_radar" not in kernel_capability_names([])
    assert "market_radar" in kernel_capability_names(["market-radar"])


def test_market_radar_routes_and_renders():
    def fn(query):
        return {"query": query, "n_scanned": 22, "n_deep": 22,
                "movers": [{"question": "LeBron to GSW?", "price": 0.33, "change": 0.10, "volume_24h": 50000}],
                "near_resolution": [{"question": "France win 7-18?", "price": 0.50, "days": 2.0,
                                     "liquidity": 2000000, "volume_24h": 900000}],
                "fresh": [{"question": "Spain vs Argentina score?", "price": 0.03, "n_candles": 19}]}

    llm = FakeLLM('{"action":"call","capability":"market_radar"}',
                  '{"action":"final","answer":"今天有几个异动"}')
    res = KernelController([market_radar_capability(fn)], llm).run("今天有什么异动，从哪找机会")
    assert [s.capability for s in res.trace] == ["market_radar"]
    r = res.facts["market_radar"]
    assert r["movers"][0]["change"] == 0.10
    md = server._format_radar(r, "market_radar")
    assert "异动" in md and "临近结算" in md and "短历史" in md
