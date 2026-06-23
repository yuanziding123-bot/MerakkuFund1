"""The supervisor — the *main* agent that owns the agent loop.

It does not do domain work itself; it dispatches to sub-agents. Each turn:

    1. ask the Router which sub-agent to run next (or stop),
    2. run it against the shared Blackboard,
    3. record the result; stop if the sub-agent halts or the goal is done.

Routing is pluggable — that is the seam between "run a fixed strategy" and "let
an LLM decide the next move":

    * :class:`SequentialRouter` — walk a fixed plan (deterministic, no tokens).
      This is the "run a strategy pipeline" path (data → signal → risk → ...).
    * :class:`CallbackRouter` — wrap any ``fn(bb, agents) -> name | None``. Drop
      an LLM-backed chooser in here later without changing the loop.
"""
from __future__ import annotations

from typing import Callable, Protocol

from .base import SubAgent
from .blackboard import AgentResult, Blackboard


class Router(Protocol):
    """Chooses the next sub-agent to run, or ``None`` to end the loop."""
    def next_agent(self, bb: Blackboard, agents: dict[str, SubAgent]) -> str | None: ...


class SequentialRouter:
    """Walk a fixed plan of sub-agent names — the deterministic strategy path."""
    def __init__(self, plan: list[str]) -> None:
        self.plan = list(plan)

    def next_agent(self, bb: Blackboard, agents: dict[str, SubAgent]) -> str | None:
        i = len(bb.trace)                   # one trace entry per executed step
        return self.plan[i] if i < len(self.plan) else None


class CallbackRouter:
    """Adapt an arbitrary decision function into a Router (the LLM-routing seam)."""
    def __init__(self, fn: Callable[[Blackboard, dict], str | None]) -> None:
        self._fn = fn

    def next_agent(self, bb: Blackboard, agents: dict[str, SubAgent]) -> str | None:
        return self._fn(bb, agents)


class Supervisor:
    """Main agent: dispatches sub-agents in a bounded loop and returns the board.

    ``on_event`` (optional) is called with small dicts as the loop runs, so a
    chat layer can stream "agent started / agent finished" to the UI.
    """
    def __init__(self, agents: list[SubAgent], router: Router | None = None,
                 max_iters: int = 12,
                 on_event: Callable[[dict], None] | None = None) -> None:
        self.agents: dict[str, SubAgent] = {a.name: a for a in agents}
        self.router = router or SequentialRouter(list(self.agents))
        self.max_iters = max_iters
        self.on_event = on_event

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass                         # telemetry must never break the loop

    def run(self, goal: str, market=None, config: dict | None = None,
            bb: Blackboard | None = None) -> Blackboard:
        bb = bb or Blackboard(goal=goal, market=market, config=config or {})
        self._emit({"type": "run_start", "goal": goal})
        for _ in range(self.max_iters):
            if bb.done:
                break
            name = self.router.next_agent(bb, self.agents)
            if name is None:
                break
            agent = self.agents.get(name)
            if agent is None:
                bb.record(AgentResult(name, ok=False,
                                      summary=f"unknown sub-agent '{name}'", halt=True))
                break
            self._emit({"type": "agent_start", "agent": name,
                        "description": agent.description})
            try:
                result = agent.run(bb)
            except Exception as exc:         # a flaky sub-agent halts, never crashes the loop
                result = AgentResult(name, ok=False, summary=f"error: {exc}", halt=True)
            bb.record(result)
            self._emit({"type": "agent_result", "agent": name, "ok": result.ok,
                        "summary": result.summary, "halt": result.halt})
            if result.halt:
                break
        self._emit({"type": "run_end", "summary": bb.summary()})
        return bb
