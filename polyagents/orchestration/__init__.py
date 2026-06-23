"""Main/sub-agent orchestration — a supervisor that runs a strategy via an
agent loop, dispatching to specialist sub-agents.

    main agent (Supervisor)  ──loop──►  data ─► signal ─► risk ─► execution
                                          (sub-agents over the L1-L4 engine)

The supervisor owns the loop and routing; sub-agents own the domain work and
share a Blackboard. Routing is pluggable (fixed strategy now, LLM router later).

Quick start (deterministic risk only, no graph needed)::

    from polyagents.orchestration import Supervisor, RiskAgent, SequentialRouter
    sup = Supervisor([RiskAgent(config)], SequentialRouter(["risk"]))
    board = sup.run("size this", config=config)

Full pipeline over the engine::

    from polyagents.graph.orchestrator import PolyAgentsGraph
    from polyagents.orchestration import build_supervisor
    g = PolyAgentsGraph()
    board = build_supervisor(graph=g, config=g.config, strategy="full").run(
        goal="trade idea", market=g.most_active_market())
    print(board.summary())
"""
from __future__ import annotations

from typing import Any

from .base import SubAgent
from .blackboard import AgentResult, Blackboard
from .subagents import DataAgent, ExecutionAgent, RiskAgent, SignalAgent
from .supervisor import CallbackRouter, Router, SequentialRouter, Supervisor

#: Named strategies = ordered sub-agent plans the supervisor walks.
STRATEGIES: dict[str, list[str]] = {
    "research": ["data"],                       # L1 only, no tokens
    "signal":   ["data", "signal"],             # + probability read (LLM)
    "full":     ["data", "signal", "risk"],     # + deterministic sizing
    "trade":    ["data", "signal", "risk", "execution"],
}


def build_supervisor(*, graph: Any = None, config: dict | None = None,
                     strategy: str = "full", **kwargs) -> Supervisor:
    """Assemble the standard sub-agents into a Supervisor for ``strategy``.

    ``graph`` (a PolyAgentsGraph) is required for any plan containing data /
    signal / execution; ``RiskAgent`` only needs ``config``.
    """
    config = config or {}
    plan = STRATEGIES.get(strategy, STRATEGIES["full"])
    agents: list[SubAgent] = []
    if "data" in plan:
        agents.append(DataAgent(graph))
    if "signal" in plan:
        agents.append(SignalAgent(graph))
    if "risk" in plan:
        agents.append(RiskAgent(config))
    if "execution" in plan:
        agents.append(ExecutionAgent(graph))
    return Supervisor(agents, SequentialRouter(plan), **kwargs)


def run_strategy(market, *, graph: Any, config: dict, strategy: str = "full",
                 on_event=None) -> Blackboard:
    """One-call convenience: run ``strategy`` end-to-end on ``market``."""
    sup = build_supervisor(graph=graph, config=config, strategy=strategy, on_event=on_event)
    goal = f"run '{strategy}' on {getattr(market, 'question', market)}"
    return sup.run(goal=goal, market=market, config=config)


__all__ = [
    "AgentResult", "Blackboard", "SubAgent",
    "DataAgent", "SignalAgent", "RiskAgent", "ExecutionAgent",
    "Supervisor", "Router", "SequentialRouter", "CallbackRouter",
    "STRATEGIES", "build_supervisor", "run_strategy",
]
