"""news_to_markets — reverse of news_sentiment (news → affected markets + direction).
Lives in the news-events pack. Worker faked (no LLM / network)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import news_to_markets_capability
from polyagents.kernel.packs import CORE, PACKS, kernel_capability_names
from polyagents.web import server


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_news_to_markets_in_news_events_pack():
    assert PACKS["news-events"]["capabilities"] == ["news_sentiment", "news_to_markets"]
    assert "news_to_markets" not in CORE
    assert "news_to_markets" in kernel_capability_names(["news-events"])


def test_news_to_markets_routes_and_renders():
    def fn(query):
        return {"query": query, "terms": ["argentina", "spain"],
                "candidates": [{"question": "Spain 3-0 Argentina?", "price": 0.03, "hits": 2},
                               {"question": "Will Argentina win WC?", "price": 0.41, "hits": 1}],
                "analysis": "- 📉 Argentina win WC: 核心伤退,夺冠承压"}

    llm = FakeLLM('{"action":"call","capability":"news_to_markets"}',
                  '{"action":"final","answer":"映射到2个标的"}')
    res = KernelController([news_to_markets_capability(fn)], llm).run("阿根廷前锋受伤,影响哪些标的")
    r = res.facts["news_markets"]
    assert len(r["candidates"]) == 2
    md = server._format_news_markets(r, "news_to_markets")
    assert "方向研判" in md and "Argentina" in md and "待验证" in md
