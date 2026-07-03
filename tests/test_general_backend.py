"""General-mode backend — selection + Alpha DevBox (pi) SSE relay (mocked HTTP)."""
from __future__ import annotations

import asyncio

import httpx

from polyagents.web.general_backend import (
    _extract_delta, chosen_general_backend, stream_devbox_general,
)


def _collect(agen):
    async def run():
        return [ev async for ev in agen]
    return asyncio.run(run())


def test_backend_selection(monkeypatch):
    monkeypatch.delenv("ASK_GENERAL_BACKEND", raising=False)
    monkeypatch.delenv("DEVBOX_BASE_URL", raising=False)
    assert chosen_general_backend() == "claude"                 # default
    monkeypatch.setenv("ASK_GENERAL_BACKEND", "devbox")
    assert chosen_general_backend() == "claude"                 # selected but no base URL
    monkeypatch.setenv("DEVBOX_BASE_URL", "http://localhost:18092")
    assert chosen_general_backend() == "devbox"


def test_extract_delta():
    assert _extract_delta({"type": "text-delta", "delta": "hi"}) == "hi"
    assert _extract_delta({"type": "text-delta", "textDelta": "yo"}) == "yo"
    assert _extract_delta({"type": "start"}) == ""


def test_devbox_relays_text_deltas(monkeypatch):
    lines = ['data: {"type":"start"}',
             'data: {"type":"text-delta","id":"1","delta":"Hello "}',
             'data: {"type":"text-delta","id":"1","delta":"world"}',
             'data: [DONE]']

    class _Resp:
        status_code = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def aiter_lines(self):
            for ln in lines:
                yield ln

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("DEVBOX_BASE_URL", "http://x")
    evs = _collect(stream_devbox_general("hi"))
    assert "".join(e["text"] for e in evs if e["type"] == "token") == "Hello world"


def test_unconfigured_yields_error(monkeypatch):
    monkeypatch.delenv("DEVBOX_BASE_URL", raising=False)
    evs = _collect(stream_devbox_general("hi"))
    assert evs and evs[0]["type"] == "error"
