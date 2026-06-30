"""A general, goal-directed agent-loop kernel (not bound to LangGraph).

The loop decides *at runtime* which capability to invoke next by reasoning
backward from the goal over each capability's preconditions/effects — so it takes
the minimal path (e.g. data → backtest) without a hard-coded flow and without
enumerating every possible request. See docs/product/agent-loop-kernel-PRD.md.
"""
from .core import AgentLoop, Capability, Context, Goal, Step, next_capability

__all__ = ["AgentLoop", "Capability", "Context", "Goal", "Step", "next_capability"]
