"""A general, goal-directed agent-loop kernel (not bound to LangGraph).

The loop decides *at runtime* which capability to invoke next by reasoning
backward from the goal over each capability's preconditions/effects — so it takes
the minimal path (e.g. data → backtest) without a hard-coded flow and without
enumerating every possible request. See docs/product/agent-loop-kernel-PRD.md.
"""
from .capabilities import (answer_capability, backtest_capability, build_registry,
                           data_capability, demo_registry, domain_capability,
                           strategy_capability)
from .controller import KernelController, KernelResult
from .core import AgentLoop, Capability, Context, Goal, Step, next_capability
from .intent import recognize
from .llm_planner import make_llm_planner
from .modes import MODES, registry_for
from .run import run_mode

__all__ = [
    "AgentLoop", "Capability", "Context", "Goal", "Step", "next_capability",
    "make_llm_planner", "recognize", "build_registry", "demo_registry",
    "data_capability", "backtest_capability", "answer_capability", "domain_capability",
    "strategy_capability", "KernelController", "KernelResult", "run_mode",
    "registry_for", "MODES",
]
