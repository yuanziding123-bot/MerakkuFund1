"""Goal-2 acceptance — a topic/event request runs the loop end to end:
discover_markets → recommend_markets, producing a ranked recommendation. Driven by
a scripted fake LLM (no network); capabilities use injected fakes."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import (discover_markets_capability,
                                             recommend_markets_capability)


class FakeLLM:
    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[-1][1])
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def _registry(captured):
    def discover(topic):
        captured["topic"] = topic
        return {"topic": topic, "count": 2, "markets": [
            {"token_id": "t1", "question": "Team A wins?"},
            {"token_id": "t2", "question": "Team B wins?"}]}

    def recommend(candidates):
        captured["candidates"] = candidates
        ranked = [
            {"token_id": "t1", "question": "Team A wins?", "action": "buy",
             "edge": 0.09, "p_true": 0.6, "annualized_edge": 0.5},
            {"token_id": "t2", "question": "Team B wins?", "action": "hold",
             "edge": 0.01, "p_true": 0.3, "annualized_edge": 0.05}]
        return {"topic": candidates.get("topic"), "n_scored": 2,
                "ranked": ranked, "top_pick": ranked[0]}

    return [discover_markets_capability(discover), recommend_markets_capability(recommend)]


def test_topic_discovers_then_recommends():
    captured: dict = {}
    llm = FakeLLM('{"action":"call","capability":"discover_markets"}',
                  '{"action":"call","capability":"recommend_markets"}',
                  '{"action":"final","answer":"推荐 Team A"}')
    res = KernelController(_registry(captured), llm).run("最近关于冠军的热点,推荐个 Polymarket 标的")
    assert [s.capability for s in res.trace] == ["discover_markets", "recommend_markets"]
    rec = res.facts["recommendation"]
    assert rec["top_pick"]["token_id"] == "t1"          # actionable buy ranked first
    assert rec["n_scored"] == 2
    assert captured["candidates"]["count"] == 2         # candidates flowed from discover


def test_recommend_hidden_until_discover_ran():
    captured: dict = {}
    llm = FakeLLM('{"action":"call","capability":"discover_markets"}',
                  '{"action":"final","answer":"ok"}')
    KernelController(_registry(captured), llm).run("推荐标的", question="推荐标的")
    assert "discover_markets" in llm.prompts[0] and "recommend_markets" not in llm.prompts[0]
    assert "recommend_markets" in llm.prompts[1]         # appears only after candidates exist
