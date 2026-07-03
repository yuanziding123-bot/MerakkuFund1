"""Ask question router — classification (rules + Haiku fallback) + handler split."""
from __future__ import annotations

from types import SimpleNamespace

from polyagents.web.agent import WRITE_TOOLS, _TOOL_FUNCS, web_search
from polyagents.web.router import classify


def test_domain_keywords_route_to_domain():
    assert classify("scan the most active markets")[0] == "domain"
    assert classify("我们最近评估跑赢市场了吗")[0] == "domain"
    assert classify("what's the calibration on the Fed market")[0] == "domain"


def test_general_keywords_route_to_general():
    assert classify("帮我写一段 python 脚本")[0] == "general"
    assert classify("explain what a transformer is")[0] == "general"
    assert classify("translate this paragraph")[0] == "general"


def test_manual_choice_wins():
    assert classify("anything at all", manual="general") == ("general", "manual")
    assert classify("write me code", manual="domain") == ("domain", "manual")


def test_ambiguous_falls_back_to_default_or_llm():
    assert classify("hmm, not sure", llm=None) == ("domain", "default")   # default = domain

    class _LLM:
        def invoke(self, _msgs):
            return SimpleNamespace(content="general")
    assert classify("hmm, not sure", llm=_LLM()) == ("general", "llm")


def test_domain_wins_on_keyword_overlap():
    # has both "explain" (general) and "market"/"probability" (domain) -> domain
    assert classify("explain the probability on this market")[0] == "domain"


def test_web_search_degrades_without_key(monkeypatch):
    from polyagents.web import agent
    monkeypatch.setitem(agent.DEFAULT_CONFIG, "tavily_api_key", None)
    out = web_search("anything")
    assert "unavailable" in out.lower() or "no tavily" in out.lower()


def test_general_tool_is_separate_from_domain_and_not_a_write_tool():
    domain_names = {f.__name__ for f in _TOOL_FUNCS}
    assert "web_search" not in domain_names      # General's tool isn't a Domain tool
    assert "web_search" not in WRITE_TOOLS        # and it's read-only
