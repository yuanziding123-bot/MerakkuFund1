"""Web: the composer 'Kernel' mode streams the request through the kernel loop."""
from __future__ import annotations

import asyncio
import json

import polyagents.kernel as kernel
from polyagents.kernel.core import Context, Goal, Step
from polyagents.web import server


def _fake_run_mode(mode, *, request=None, on_event=None, **kw):
    # emit the same events a real loop would, then return a finished Context
    on_event({"type": "loop.start", "goal": ["answer"], "label": "ask"})
    on_event({"type": "capability.start", "name": "langgraph_answer"})
    on_event({"type": "capability.done", "name": "langgraph_answer", "produced": ["answer"]})
    on_event({"type": "loop.end", "done": True, "path": ["langgraph_answer"]})
    ctx = Context(Goal(frozenset({"answer"}), {"question": request}, "ask"))
    ctx.facts["answer"] = f"kernel-answered: {request}"
    ctx.trace.append(Step("langgraph_answer", ["answer"], ok=True))
    return ctx


def _streaming_run_mode(mode, *, request=None, on_event=None, **kw):
    # a capability that streams inner tokens live (Step 4/5 contract)
    on_event({"type": "capability.start", "name": "langgraph_answer"})
    for tok in ["Hel", "lo"]:
        on_event({"type": "token", "text": tok})
    on_event({"type": "capability.done", "name": "langgraph_answer", "produced": ["answer"]})
    ctx = Context(Goal(frozenset({"answer"}), {"question": request}, "kernel"))
    ctx.facts["answer"] = "Hello"                       # same content already streamed
    ctx.trace.append(Step("langgraph_answer", ["answer"], ok=True))
    return ctx


def _collect(history, mode, monkeypatch, fake=_fake_run_mode):
    monkeypatch.setattr(kernel, "run_mode", fake)

    async def go():
        return [json.loads(s.split("data:", 1)[1].strip())
                for s in [x async for x in server._stream(history, [], mode=mode)]]

    return asyncio.run(go())


def test_kernel_mode_routes_through_the_loop(monkeypatch):
    events = _collect([{"role": "user", "content": "什么是校准"}], "kernel", monkeypatch)
    types = [e["type"] for e in events]

    route = next(e for e in events if e["type"] == "route")
    assert route["route"] == "kernel" and route["by"] == "manual"       # not domain/general
    assert "tool" in types and "tool_result" in types                    # capability shown as tool
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["name"] == "langgraph_answer"                            # ReAct ran as a capability
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "kernel-answered: 什么是校准" in text
    assert types[-1] == "done"


def test_kernel_mode_forwards_live_tokens_without_duplicating(monkeypatch):
    events = _collect([{"role": "user", "content": "hi"}], "kernel", monkeypatch,
                      fake=_streaming_run_mode)
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert text == "Hello"          # streamed once, NOT re-appended from _kernel_summary
    assert [e["type"] for e in events][-1] == "done"


def test_non_kernel_mode_untouched(monkeypatch):
    # a bomb: if classify path is (wrongly) skipped for non-kernel modes this fires
    called = {"n": 0}
    monkeypatch.setattr(kernel, "run_mode",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(server, "classify", lambda *a, **k: ("domain", "rule"))
    monkeypatch.setattr(server, "build_agent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))

    async def go():
        return [x async for x in server._stream([{"role": "user", "content": "hi"}], [], mode="auto")]

    out = asyncio.run(go())
    assert called["n"] == 0                                              # kernel not invoked
    assert any('"route": "domain"' in s for s in out)
