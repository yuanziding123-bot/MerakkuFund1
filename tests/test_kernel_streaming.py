"""Step 4 — streaming capability contract: a capability's inner tokens flow out
live via emit (breaking the double black box), while non-stream capabilities and
the no-on_event path keep the plain run(ctx)->dict behaviour."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import answer_capability


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def _streaming_answer_fn(question, emit):
    for tok in ["Hel", "lo ", question]:               # pretend inner-model tokens
        emit({"type": "token", "text": tok})
    return "Hello " + question


def test_capability_stream_field_is_set_when_stream_fn_given():
    cap = answer_capability(lambda q: q, stream_fn=_streaming_answer_fn)
    assert cap.stream is not None
    assert answer_capability(lambda q: q).stream is None      # opt-in only


def test_controller_streams_inner_tokens_when_on_event_present():
    events: list[dict] = []
    llm = FakeLLM('{"action":"call","capability":"langgraph_answer"}',
                  '{"action":"final","answer":"done"}')
    reg = [answer_capability(lambda q: q, stream_fn=_streaming_answer_fn)]
    res = KernelController(reg, llm, on_event=events.append).run("world")
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert tokens == ["Hel", "lo ", "world"]                  # inner tokens flowed out live
    assert res.facts["answer"] == "done"                      # capability still returned facts
    assert [s.capability for s in res.trace] == ["langgraph_answer"]


def test_no_on_event_falls_back_to_blocking_run():
    # without an on_event sink the loop must use run() (no streaming), unchanged
    called = {"run": 0}

    def answer_fn(q):
        called["run"] += 1
        return "blocking:" + q

    llm = FakeLLM('{"action":"call","capability":"langgraph_answer"}',
                  '{"action":"final","answer":"done"}')
    reg = [answer_capability(answer_fn, stream_fn=_streaming_answer_fn)]
    res = KernelController(reg, llm).run("x")                  # no on_event
    assert called["run"] == 1                                  # blocking run() used
    assert [s.capability for s in res.trace] == ["langgraph_answer"]
