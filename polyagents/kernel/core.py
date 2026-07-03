"""Kernel core — Capability, Goal/Context, the planner, and the AgentLoop.

The design borrows from coding agents (one loop, a capability registry, a planner
that picks the next action each step) but is **goal-directed**: capabilities
declare what they need (``preconditions``) and produce (``effects``), and the
planner reasons *backward from the goal* to pick the minimal runnable next step.
Adding a capability never touches the loop — if it lies on a goal's reachable
chain, the planner uses it automatically. No LLM and no LangGraph in the core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Capability:
    """One unit of work — an agent, a tool, or a sub-loop.

    ``run(ctx)`` reads what it needs from ``ctx.facts`` and returns a dict of new
    facts (its ``effects``). ``preconditions`` / ``effects`` are fact keys.
    """
    name: str
    description: str
    preconditions: frozenset[str]
    effects: frozenset[str]
    run: Callable[["Context"], dict]
    cost: int = 1
    # Optional streaming variant: ``stream(ctx, emit) -> facts`` where ``emit`` is
    # called with token/tool events as the work happens. When present (and the loop
    # has an ``on_event`` sink) it is preferred over ``run`` — this is what breaks
    # the "double black box": a capability's inner tokens flow out live instead of
    # only its final return value.
    stream: Callable[["Context", Callable[[dict], None]], dict] | None = None


@dataclass(frozen=True)
class Goal:
    """What we want produced (``targets``) plus the facts we start with."""
    targets: frozenset[str]
    facts: dict = field(default_factory=dict)
    label: str = ""


@dataclass
class Step:
    capability: str
    produced: list[str]
    ok: bool = True
    note: str = ""


@dataclass
class Context:
    """The blackboard: known facts + the goal + an audit trace."""
    goal: Goal
    facts: dict = field(default_factory=dict)
    trace: list[Step] = field(default_factory=list)

    def __post_init__(self) -> None:
        # start from the goal's initial facts (without mutating the goal)
        self.facts = {**self.goal.facts, **self.facts}

    @property
    def known(self) -> set[str]:
        return set(self.facts)

    def done(self) -> bool:
        return self.goal.targets <= self.known


# ----- planner ---------------------------------------------------------------

def _needed_effects(targets: frozenset[str], registry: list[Capability],
                    known: set[str]) -> set[str]:
    """Backward reachability: every effect that must still be produced to reach
    the goal from ``known`` (targets, then their providers' preconditions, …)."""
    need = set(targets) - known
    frontier = set(need)
    while frontier:
        e = frontier.pop()
        for cap in registry:
            if e in cap.effects:
                for p in cap.preconditions:
                    if p not in known and p not in need:
                        need.add(p)
                        frontier.add(p)
    return need


def next_capability(ctx: Context, registry: list[Capability]) -> Capability | None:
    """Pick the next capability to run, or None to stop.

    Choose a capability that (a) is runnable now (preconditions ⊆ known), (b)
    produces something still needed for the goal, preferring the cheapest. Returns
    None when the goal is met or nothing is runnable (the latter is where a P2
    LLM planner would step in)."""
    if ctx.done():
        return None
    need = _needed_effects(ctx.goal.targets, registry, ctx.known)
    runnable = [c for c in registry
                if (c.effects & need) and c.preconditions <= ctx.known
                and not (c.effects <= ctx.known)]
    if not runnable:
        return None
    return min(runnable, key=lambda c: (c.cost, c.name))


# ----- the loop --------------------------------------------------------------

class AgentLoop:
    """perceive → plan → act → observe, bounded, audited.

    ``registry`` is the capability set (a *mode* = a particular subset).
    ``planner`` is swappable (deterministic by default; an LLM planner can be
    dropped in later). ``audit`` exposes ``.log(...)``; ``on_event`` streams.
    """
    def __init__(self, registry: list[Capability], *, max_steps: int = 12,
                 planner: Callable = next_capability, fallback_planner: Callable | None = None,
                 audit=None, on_event: Callable[[dict], None] | None = None) -> None:
        self.registry = list(registry)
        self.max_steps = max_steps
        self.planner = planner                 # deterministic goal-directed (zero tokens)
        self.fallback_planner = fallback_planner  # e.g. an LLM planner for the ambiguous cases
        self.audit = audit
        self.on_event = on_event

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def _audit(self, event_type: str, **payload) -> None:
        if self.audit is not None:
            try:
                self.audit.log("kernel", event_type, payload, mode=self.registry and "loop" or "")
            except Exception:
                pass

    def run(self, goal: Goal) -> Context:
        ctx = Context(goal)
        self._emit({"type": "loop.start", "goal": sorted(goal.targets), "label": goal.label})
        self._audit("loop.start", goal=sorted(goal.targets), label=goal.label)
        for _ in range(self.max_steps):
            cap = self.planner(ctx, self.registry)
            if cap is None and self.fallback_planner is not None and not ctx.done():
                cap = self.fallback_planner(ctx, self.registry)   # LLM decides the ambiguous step
                if cap is not None:
                    self._audit("planner.fallback", capability=cap.name)
            if cap is None:
                break
            self._emit({"type": "capability.start", "name": cap.name})
            try:
                produced = cap.run(ctx) or {}
            except Exception as exc:
                ctx.trace.append(Step(cap.name, [], ok=False, note=str(exc)))
                self._audit("capability.error", capability=cap.name, error=str(exc))
                self._emit({"type": "capability.error", "name": cap.name, "error": str(exc)})
                break
            ctx.facts.update(produced)
            keys = list(produced)
            ctx.trace.append(Step(cap.name, keys, ok=True))
            self._audit("capability.ran", capability=cap.name, produced=keys)
            self._emit({"type": "capability.done", "name": cap.name, "produced": keys})
        self._emit({"type": "loop.end", "done": ctx.done(),
                    "path": [s.capability for s in ctx.trace]})
        self._audit("loop.end", done=ctx.done(), path=[s.capability for s in ctx.trace])
        return ctx
