"""AgentSession — one field (``mode``) decides three things (v0.2 PRD §八-B).

    session = AgentSession(mode="ask")
    session.readonly        # → which tool subset to inject (ToolManifest)
    session.policy          # → what's allowed (PermissionPolicy)
    session.log(...)        # → audit strength (to an AuditSink)

There is no "three engines" — just this small coordinator plus three configs.
``ask`` is read-only (no trading), ``lab`` is the paper sandbox, ``live`` is the
gated real-money mode. The session does NOT build the LLM agent itself; the web
layer reads ``session.readonly`` and calls ``build_tools(readonly=...)`` — keeping
this module free of LLM / web imports so it stays trivially testable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

Mode = Literal["ask", "lab", "live"]


@dataclass(frozen=True)
class PermissionPolicy:
    """What a mode may do. The tool subset enforces it; this documents/guards it."""
    mode: Mode
    readonly: bool          # inject only read-only tools (no side effects)
    can_trade: bool         # place real/paper orders
    can_promote: bool       # create/advance objects (gate transitions)
    audit_tool_calls: bool  # log every tool.call, not just session start/end

    @classmethod
    def for_mode(cls, mode: str) -> "PermissionPolicy":
        if mode == "ask":
            return cls("ask", readonly=True, can_trade=False, can_promote=True,
                       audit_tool_calls=True)
        if mode == "lab":
            return cls("lab", readonly=False, can_trade=False, can_promote=True,
                       audit_tool_calls=True)
        if mode == "live":
            return cls("live", readonly=False, can_trade=True, can_promote=False,
                       audit_tool_calls=True)
        raise ValueError(f"unknown mode: {mode!r}")


class AgentSession:
    """A mode-scoped run context with an audit trail.

    ``audit`` is any object with ``.log(session_id, event_type, payload, mode)``
    (e.g. :class:`polyagents.storage.audit_store.AuditStore`), or None.
    """
    def __init__(self, mode: str = "ask", *, tenant: str = "default", audit=None) -> None:
        self.id = "s_" + uuid.uuid4().hex[:10]
        self.mode = mode
        self.tenant = tenant
        self.policy = PermissionPolicy.for_mode(mode)
        self.audit = audit

    @property
    def readonly(self) -> bool:
        return self.policy.readonly

    def log(self, event_type: str, **payload) -> None:
        if self.audit is not None:
            try:
                self.audit.log(self.id, event_type, payload, mode=self.mode)
            except Exception:
                pass            # audit must never break the run
