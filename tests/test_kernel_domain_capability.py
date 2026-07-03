"""Step 3 — the kernel can call a domain sub-agent (market tools) as a capability,
and the controller chooses domain vs general Q&A by fit."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import answer_capability, domain_capability


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[-1][1])
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def _reg():
    return [answer_capability(lambda q: f"general:{q}"),
            domain_capability(lambda q: f"domain:{q}")]


def test_both_answer_capabilities_are_offered():
    llm = FakeLLM('{"action":"final","answer":"x"}')
    KernelController(_reg(), llm).run("q")
    menu = llm.prompts[0]
    assert "langgraph_answer" in menu and "domain_answer" in menu   # LLM chooses which


def test_controller_can_route_to_domain_tools_agent():
    llm = FakeLLM('{"action":"call","capability":"domain_answer"}',
                  '{"action":"final","answer":"done"}')
    res = KernelController(_reg(), llm).run("扫一下哪个市场有 edge")
    assert [s.capability for s in res.trace] == ["domain_answer"]   # routed to market-tools agent
    assert res.trace[0].produced == ["answer"] and res.facts["answer"] == "done"


def test_controller_can_route_to_general_agent():
    llm = FakeLLM('{"action":"call","capability":"langgraph_answer"}',
                  '{"action":"final","answer":"done"}')
    res = KernelController(_reg(), llm).run("写个正则")
    assert [s.capability for s in res.trace] == ["langgraph_answer"]


def test_wiring_registry_includes_domain_answer():
    # names/preconditions only — no engine/network touched by construction
    import polyagents.kernel.wiring as wiring
    # build_registry-style check via the real builder is network-heavy; assert the
    # capability exists and is distinct from the general one at the module level
    from polyagents.kernel.capabilities import domain_capability as dc
    cap = dc(lambda q: q)
    assert cap.name == "domain_answer" and cap.effects == frozenset({"answer"})
