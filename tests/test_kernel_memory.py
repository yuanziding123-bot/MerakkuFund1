"""Cross-turn memory — kernel mode sees the prior conversation, not just the last
line, so it can resolve references to earlier turns."""
from __future__ import annotations

import asyncio
import json

import polyagents.kernel as kernel
from polyagents.kernel import run_mode
from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import answer_capability
from polyagents.kernel.core import Context, Goal, Step
from polyagents.web import server


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[-1][1])
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_controller_prompt_includes_prior_turns():
    llm = FakeLLM('{"action":"final","answer":"英伟达"}')
    hist = [("user", "英伟达今天涨了吗"), ("assistant", "涨了 3%")]
    KernelController([answer_capability(lambda q: q)], llm).run("那台积电呢", history=hist)
    p = llm.prompts[0]
    assert "Conversation so far" in p
    assert "英伟达今天涨了吗" in p and "涨了 3%" in p          # earlier turns are in context
    assert "那台积电呢" in p                                   # current request too


def test_no_history_omits_the_conversation_block():
    llm = FakeLLM('{"action":"final","answer":"x"}')
    KernelController([answer_capability(lambda q: q)], llm).run("hi")
    assert "Conversation so far" not in llm.prompts[0]


def test_run_mode_threads_history_to_controller():
    llm = FakeLLM('{"action":"final","answer":"ok"}')
    hist = [("user", "先前的问题"), ("assistant", "先前的回答")]
    run_mode("kernel", request="接着问", registry=[answer_capability(lambda q: q)],
             llm=llm, history=hist)
    assert "先前的回答" in llm.prompts[0]


def test_stream_kernel_splits_current_and_prior(monkeypatch):
    seen = {}

    def fake_run_mode(mode, *, request=None, history=None, on_event=None, **kw):
        seen["request"] = request
        seen["history"] = history
        on_event({"type": "capability.done", "name": "x", "produced": []})
        ctx = Context(Goal(frozenset({"answer"}), {}, "kernel"))
        ctx.facts["answer"] = "done"
        return ctx

    monkeypatch.setattr(kernel, "run_mode", fake_run_mode)
    history = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"},
               {"role": "user", "content": "Q2"}]

    async def go():
        return [x async for x in server._stream(history, [], mode="kernel")]

    asyncio.run(go())
    assert seen["request"] == "Q2"                            # current = last user msg
    assert seen["history"] == [("user", "Q1"), ("assistant", "A1")]   # prior turns only
