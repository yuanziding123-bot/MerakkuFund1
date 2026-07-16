"""news-events & microstructure vertical packs — selectable signal capabilities."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import (microstructure_scan_capability,
                                             news_sentiment_capability)
from polyagents.kernel.packs import PACKS, kernel_capability_names


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_new_packs_registered_and_selectable():
    assert PACKS["news-events"]["capabilities"] == ["news_sentiment", "news_to_markets"]
    assert PACKS["microstructure"]["capabilities"] == ["microstructure_scan"]
    names = kernel_capability_names(["microstructure"])
    assert "microstructure_scan" in names and "news_sentiment" not in names   # only selected pack


def test_news_sentiment_capability():
    def fn(q):
        return {"query": q, "enabled": True, "n_items": 2, "mean_sentiment": 0.2,
                "signal": "偏多", "items": [{"title": "up", "url": "x", "sentiment": 0.3}]}
    llm = FakeLLM('{"action":"call","capability":"news_sentiment"}',
                  '{"action":"final","answer":"情绪偏多"}')
    res = KernelController([news_sentiment_capability(fn)], llm).run("news on BTC")
    assert res.facts["news_sentiment"]["signal"] == "偏多"


def test_microstructure_scan_capability_ranks():
    def fn(q):
        return {"query": q, "category": "sports", "n_scanned": 2, "markets": [
            {"question": "A", "score": 0.7, "lean": "YES"},
            {"question": "B", "score": 0.3, "lean": "NO"}]}
    llm = FakeLLM('{"action":"call","capability":"microstructure_scan"}',
                  '{"action":"final","answer":"A 资金流最强"}')
    res = KernelController([microstructure_scan_capability(fn)], llm).run("scan order flow")
    assert res.facts["microstructure"]["markets"][0]["score"] == 0.7
