"""The sub-agent contract.

A sub-agent is one specialist the supervisor can dispatch to during the loop
(DataAgent, SignalAgent, RiskAgent, ExecutionAgent, ...). Each one reads/writes
the shared :class:`Blackboard` and returns an :class:`AgentResult`. Keeping the
surface this small is what lets new sub-agents be "filled in later" without
touching the supervisor or the loop.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .blackboard import AgentResult, Blackboard


class SubAgent(ABC):
    """One specialist agent. Set ``name`` / ``description`` on the subclass.

    ``name`` is how the supervisor and routers refer to it; ``description`` is
    what an LLM router (later) reads to decide whether to call it.
    """
    name: str = "sub"
    description: str = ""

    @abstractmethod
    def run(self, bb: Blackboard) -> AgentResult:
        """Do one unit of work against the blackboard and report back."""
        raise NotImplementedError

    # small helpers so subclasses stay terse
    def ok(self, summary: str, **output) -> AgentResult:
        return AgentResult(self.name, ok=True, summary=summary, output=output)

    def fail(self, summary: str, *, halt: bool = True, **output) -> AgentResult:
        return AgentResult(self.name, ok=False, summary=summary, halt=halt, output=output)
