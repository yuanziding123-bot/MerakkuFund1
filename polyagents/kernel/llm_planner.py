"""LLM planner — the fallback for when the deterministic planner is stuck or the
goal is fuzzy. Given the goal + known facts + the capabilities runnable *now*, ask
the model which one to run next (or stop). Injected llm, so it's unit-testable.
"""
from __future__ import annotations

import re

from .core import Capability, Context

_SYS = (
    "You are the planner of a goal-directed agent loop. Given the goal, the facts "
    "already known, and the capabilities that are runnable RIGHT NOW, choose the "
    "single next capability to run — or stop if the goal is met or nothing helps. "
    'Reply with ONLY JSON: {"next": "<capability-name|stop>"}.'
)


def make_llm_planner(llm):
    """Return a planner ``fn(ctx, registry) -> Capability | None`` backed by ``llm``."""
    def plan(ctx: Context, registry: list[Capability]) -> Capability | None:
        runnable = [c for c in registry
                    if c.preconditions <= ctx.known and not (c.effects <= ctx.known)]
        if not runnable:
            return None
        menu = "\n".join(
            f"- {c.name}: {c.description} (needs {sorted(c.preconditions)}, "
            f"produces {sorted(c.effects)})" for c in runnable)
        user = (f"goal: {sorted(ctx.goal.targets)} (label: {ctx.goal.label})\n"
                f"known facts: {sorted(ctx.known)}\n\nrunnable now:\n{menu}\n\nnext? JSON only.")
        try:
            resp = llm.invoke([("system", _SYS), ("user", user)])
            text = getattr(resp, "content", resp)
            if isinstance(text, list):
                text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
            m = re.search(r'"next"\s*:\s*"([^"]+)"', str(text))
            name = (m.group(1) if m else "").strip().lower()
        except Exception:
            return None
        if name in ("", "stop"):
            return None
        return next((c for c in runnable if c.name.lower() == name), None)
    return plan
