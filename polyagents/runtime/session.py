"""Minimal runtime permissions for AIHF modes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class PermissionDenied(RuntimeError):
    code = "permission_denied"

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"permission denied for tool: {tool_name}")
        self.tool_name = tool_name


@dataclass(frozen=True)
class PermissionPolicy:
    mode: str
    allowed_tools: set[str]

    @classmethod
    def for_mode(cls, mode: str) -> "PermissionPolicy":
        tools_by_mode = {
            "ask": {"scan_markets", "market_snapshot", "evaluate_forecast"},
            "lab": {
                "scan_markets",
                "market_snapshot",
                "evaluate_forecast",
                "create_hypothesis",
                "run_backtest",
                "write_forecast",
                "write_evaluation",
            },
            "live": {"market_snapshot", "evaluate_forecast", "submit_order", "halt"},
        }
        if mode not in tools_by_mode:
            raise ValueError(f"unknown mode: {mode}")
        return cls(mode=mode, allowed_tools=tools_by_mode[mode])

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


@dataclass
class AuditSink:
    events: list[dict[str, Any]] = field(default_factory=list)

    def log(self, event_type: str, **payload: Any) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


@dataclass
class AgentSession:
    mode: str
    tenant: str = "default"
    audit: AuditSink = field(default_factory=AuditSink)

    def __post_init__(self) -> None:
        self.permissions = PermissionPolicy.for_mode(self.mode)

    def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.permissions.allows(tool_name):
            self.audit.log("permission.denied", tool=tool_name, args=args, mode=self.mode)
            raise PermissionDenied(tool_name)
        self.audit.log("tool.call", tool=tool_name, args=args, mode=self.mode)
        return {"ok": True, "tool": tool_name}
