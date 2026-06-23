"""The shared run context (blackboard) the supervisor and sub-agents read/write.

The whole main/sub-agent framework is "deterministic-first": sub-agents
communicate through this one mutable object, not by passing ad-hoc kwargs around.
It mirrors the LangGraph ``MarketState`` blackboard the L1-L4 pipeline already
uses, but at the *orchestration* level — one strategy run = one Blackboard, and
its ``trace`` is the audit log of every sub-agent the supervisor dispatched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """What a sub-agent hands back to the supervisor after one turn of the loop."""
    agent: str
    ok: bool = True
    summary: str = ""                       # one line, for the chat / trace
    output: dict = field(default_factory=dict)
    halt: bool = False                      # ask the loop to stop (risk veto / hard error)


@dataclass
class Blackboard:
    """Shared state for one strategy run.

    Sub-agents read what they need (e.g. RiskAgent reads ``signal`` + ``data``)
    and write their findings back (``signal`` / ``risk`` / ``execution``). The
    supervisor never inspects domain fields — it only walks ``trace`` and watches
    ``done`` / ``AgentResult.halt``.
    """
    goal: str
    market: Any | None = None               # a dataflows.types.Market (or None for Ask)
    config: dict = field(default_factory=dict)

    # findings — each sub-agent owns one slot (kept loose dicts so the schema can
    # evolve without touching the framework):
    data: dict = field(default_factory=dict)        # L1 snapshot: price/liquidity/spread/reports
    signal: dict | None = None                      # {direction, p_true, conviction, market_price, ...}
    risk: dict | None = None                        # {action, size_usdc, edge, apy, reasons, ...}
    execution: dict | None = None                   # {filled, shares, avg_price, ...}

    # loop control + audit:
    trace: list[AgentResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    done: bool = False

    def record(self, result: AgentResult) -> AgentResult:
        self.trace.append(result)
        return result

    def last(self, agent: str) -> AgentResult | None:
        for r in reversed(self.trace):
            if r.agent == agent:
                return r
        return None

    def summary(self) -> str:
        """Render the loop trace for the chat / logs."""
        lines = [f"strategy run · goal: {self.goal}"]
        for r in self.trace:
            mark = "✓" if r.ok else "✗"
            stop = "  [halt]" if r.halt else ""
            lines.append(f"  {mark} {r.agent}: {r.summary}{stop}")
        return "\n".join(lines)
