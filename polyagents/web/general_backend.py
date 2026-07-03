"""Pluggable backend for Ask's General answer mode.

P1 answers open-ended questions with a Claude agent (see web/agent.py). P2 lets
the General mode instead delegate to an external coding agent — **Alpha DevBox /
pi.dev** — which runs its own agent loop (coding, tools, sandbox) and streams a
Vercel-AI-SDK SSE reply. We relay that stream as our own token events.

Selection is env-driven and degrades gracefully:

    ASK_GENERAL_BACKEND=devbox          # else 'claude' (default)
    DEVBOX_BASE_URL=http://localhost:18092
    DEVBOX_USER_ID=web                  # optional

If devbox isn't configured/reachable the caller falls back to the Claude agent,
so nothing breaks when pi isn't running. The request/stream contract matches
Alpha DevBox's ``POST /api/devbox/chat`` (see its src/channels/web.ts).
"""
from __future__ import annotations

import json
import os
import uuid
from typing import AsyncIterator


def chosen_general_backend() -> str:
    """'devbox' only when explicitly selected AND a base URL is set; else 'claude'."""
    if os.getenv("ASK_GENERAL_BACKEND", "claude").lower() == "devbox" and _devbox_base():
        return "devbox"
    return "claude"


def _devbox_base() -> str | None:
    url = os.getenv("DEVBOX_BASE_URL")
    return url.rstrip("/") if url else None


def _extract_delta(obj: dict) -> str:
    """Pull text out of one AI-SDK data-stream event (text-delta carries it)."""
    if obj.get("type") == "text-delta":
        return obj.get("delta") or obj.get("textDelta") or obj.get("text") or ""
    return ""


async def stream_devbox_general(question: str, *, model: str | None = None,
                                conv_id: str | None = None) -> AsyncIterator[dict]:
    """POST the question to Alpha DevBox and relay its SSE as token/error dicts."""
    import httpx

    base = _devbox_base()
    if not base:
        yield {"type": "error", "message": "devbox backend not configured"}
        return
    user = os.getenv("DEVBOX_USER_ID", "web")
    body = {
        "id": conv_id or f"aihf_{uuid.uuid4().hex[:8]}",
        "messages": [{"id": uuid.uuid4().hex[:8], "role": "user",
                      "parts": [{"type": "text", "text": question}]}],
    }
    headers = {"Content-Type": "application/json", "X-User-Id": user}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            async with client.stream("POST", f"{base}/api/devbox/chat",
                                     json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    yield {"type": "error", "message": f"devbox HTTP {resp.status_code}"}
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data in ("", "[DONE]"):
                        continue
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    text = _extract_delta(obj)
                    if text:
                        yield {"type": "token", "text": text}
                    elif obj.get("type") == "error":
                        yield {"type": "error", "message": str(obj.get("errorText") or obj)}
    except Exception as exc:
        yield {"type": "error", "message": f"devbox unreachable: {exc}"}
