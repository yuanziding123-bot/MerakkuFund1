"""AgentSession: one mode decides tool scope, permissions, and audit behavior."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["ask", "lab", "live"]


class PermissionDenied(RuntimeError):
    code = "permission_denied"

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"permission denied for tool: {tool_name}")
        self.tool_name = tool_name


@dataclass(frozen=True)
class PermissionPolicy:
    """What a mode may do."""

    mode: Mode
    readonly: bool
    can_trade: bool
    can_promote: bool
    audit_tool_calls: bool
    allowed_tools: set[str]

    @classmethod
    def for_mode(cls, mode: str) -> "PermissionPolicy":
        if mode == "ask":
            return cls(
                "ask",
                readonly=True,
                can_trade=False,
                can_promote=True,
                audit_tool_calls=True,
                allowed_tools={"scan_markets", "market_snapshot", "evaluate_forecast"},
            )
        if mode == "lab":
            return cls(
                "lab",
                readonly=False,
                can_trade=False,
                can_promote=True,
                audit_tool_calls=True,
                allowed_tools={
                    "scan_markets",
                    "market_snapshot",
                    "evaluate_forecast",
                    "create_hypothesis",
                    "run_backtest",
                    "write_forecast",
                    "write_evaluation",
                },
            )
        if mode == "live":
            return cls(
                "live",
                readonly=False,
                can_trade=True,
                can_promote=False,
                audit_tool_calls=True,
                allowed_tools={"market_snapshot", "evaluate_forecast", "submit_order", "halt"},
            )
        raise ValueError(f"unknown mode: {mode!r}")

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


@dataclass
class LocalAuditSink:
    events: list[dict[str, Any]] = field(default_factory=list)

    def log_local(self, event_type: str, **payload: Any) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


class AgentSession:
    """A mode-scoped run context with an audit trail.

    ``audit`` may be any object exposing
    ``log(session_id, event_type, payload, mode=...)``. If omitted, an in-memory
    sink is used for tests and local permission diagnostics.
    """

    def __init__(self, mode: str = "ask", *, tenant: str = "default", audit=None) -> None:
        self.id = "s_" + uuid.uuid4().hex[:10]
        self.mode = mode
        self.tenant = tenant
        self.policy = PermissionPolicy.for_mode(mode)
        self.permissions = self.policy
        self.audit = audit if audit is not None else LocalAuditSink()

    @property
    def readonly(self) -> bool:
        return self.policy.readonly

    def log(self, event_type: str, **payload: Any) -> None:
        try:
            if hasattr(self.audit, "log_local"):
                self.audit.log_local(event_type, **payload)
            else:
                self.audit.log(self.id, event_type, payload, mode=self.mode)
        except Exception:
            pass

    def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.policy.allows(tool_name):
            self.log("permission.denied", tool=tool_name, args=args, mode=self.mode)
            raise PermissionDenied(tool_name)
        self.log("tool.call", tool=tool_name, args=args, mode=self.mode)
        return {"ok": True, "tool": tool_name}
