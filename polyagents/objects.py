"""The 5 financial objects + their state machine — the spine of AIHF v0.2.

The whole product is CRUD + state transitions over five object types::

    Market ──► Hypothesis ──► Strategy ──► Position ──► Portfolio

Per the v0.2 design, "all the complexity lives in the object state machine, not
in an architecture layer". So this module owns that complexity and nothing else:
immutable objects, a single shared contract (:class:`FO`), and *deterministic*
promotion gates (a promotion is never an LLM judgement — it is a pure function
over an :class:`EvalSummary` plus, for the human gates, an explicit caller).

Objects are frozen. "Changing a parameter" or "promoting" never mutates — it
produces a new ``version`` via :func:`promote` / :func:`revise`, appending a
:class:`PromotionEvent` to the object's lineage so every state change is
single-directional, versioned, and carries evidence.

No deps beyond the stdlib; nothing here touches the network, the LLM, or money.
Persistence (the ``objects`` / ``promotion_events`` tables) lives elsewhere.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Literal

ObjectType = Literal["market", "hypothesis", "strategy", "position", "portfolio"]
State = Literal["draft", "lab", "paper", "live", "archived"]

#: Legal state transitions. Promotion is single-directional; ``archived`` is the
#: only terminal sink (kill / rollback can happen from any non-terminal state).
ALLOWED_TRANSITIONS: dict[State, set[State]] = {
    "draft": {"lab", "archived"},
    "lab": {"paper", "archived"},
    "paper": {"live", "archived"},
    "live": {"archived"},
    "archived": set(),
}


class IllegalTransition(ValueError):
    """Raised when a promotion is not a legal edge in the state machine."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(object_type: ObjectType) -> str:
    """A short, type-prefixed id, e.g. ``hypothesis -> 'H-3f9a2c'``."""
    prefix = {"market": "M", "hypothesis": "H", "strategy": "S",
              "position": "P", "portfolio": "PF"}[object_type]
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


# ----- evidence & lineage ----------------------------------------------------

@dataclass(frozen=True)
class EvalSummary:
    """A statistically-honest snapshot of an object's edge over the market.

    ``beats_market`` requires the *lower bound* of the bootstrap CI to exclude 0
    (a positive point estimate alone is not enough); ``sample_adequate`` guards
    against calling edge on too few resolved markets. These two booleans are what
    the deterministic promotion gates read.
    """
    n: int
    brier_model: float
    brier_market: float
    brier_delta: float                      # model - market; negative = better
    brier_delta_ci: tuple[float, float]     # bootstrap 95% CI on brier_delta
    ece: float
    beats_market: bool                      # CI upper bound < 0
    sample_adequate: bool                   # n >= min_samples


@dataclass(frozen=True)
class PromotionEvent:
    """One edge traversed in the state machine, with its evidence."""
    from_state: State
    to_state: State
    promoted_by: str                        # "user:alice" / "policy:auto-eval-gate-v1"
    evidence_ref: str | None = None         # eval id / session id / transcript ref
    promoted_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class Lineage:
    """Where an object came from and how it has moved through the machine."""
    parent_id: str | None = None            # promoted/forked from this object
    events: tuple[PromotionEvent, ...] = ()


# ----- the shared contract + 5 objects ---------------------------------------

@dataclass(frozen=True)
class FO:
    """FinancialObject — the contract every object shares.

    This shared shape is *the* source of generality: a new market type or a new
    object never changes this contract, the eval pipeline, or the UI.
    """
    id: str
    type: ObjectType
    version: int
    snapshot_id: str                        # PIT snapshot hash at creation time
    state: State
    owner: str                              # tenant / user (single-tenant MVP)
    lineage: Lineage
    created_at: str
    eval_summary: EvalSummary | None = None


@dataclass(frozen=True)
class Market(FO):
    token_id: str = ""
    question: str = ""
    category: str = ""
    outcome: str = ""
    price: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Hypothesis(FO):
    statement: str = ""                     # "crypto news updates LLM faster than market"
    category_filter: str = ""
    feature_set: tuple[str, ...] = ()
    prompt_version: str = ""
    model_version: str = ""


@dataclass(frozen=True)
class Strategy(FO):
    hypothesis_id: str = ""                 # lineage parent
    calibrator_id: str = ""
    sizing_rule: dict = field(default_factory=dict)   # {kelly_fraction, max_edge_apy, ...}
    risk_gates: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Position(FO):
    strategy_id: str = ""
    market_token_id: str = ""
    side: str = ""
    size_usdc: float = 0.0
    entry_snapshot_id: str = ""


@dataclass(frozen=True)
class Portfolio(FO):
    positions: tuple[str, ...] = ()         # position ids
    nav_usdc: float = 0.0


# ----- object construction ---------------------------------------------------

_INITIAL_STATE: dict[ObjectType, State] = {
    "market": "draft", "hypothesis": "draft", "strategy": "lab",
    "position": "paper", "portfolio": "live",
}

_CLASSES: dict[ObjectType, type[FO]] = {
    "market": Market, "hypothesis": Hypothesis, "strategy": Strategy,
    "position": Position, "portfolio": Portfolio,
}


def make(object_type: ObjectType, *, snapshot_id: str, owner: str = "default",
         parent_id: str | None = None, state: State | None = None, **fields) -> FO:
    """Mint a fresh object at version 1 with an empty (or parent-rooted) lineage."""
    cls = _CLASSES[object_type]
    return cls(
        id=new_id(object_type),
        type=object_type,
        version=1,
        snapshot_id=snapshot_id,
        state=state or _INITIAL_STATE[object_type],
        owner=owner,
        lineage=Lineage(parent_id=parent_id),
        created_at=_now(),
        **fields,
    )


def revise(obj: FO, **changes) -> FO:
    """A parameter change = a new version (objects are immutable).

    Keeps the same id and state; bumps ``version``. Use :func:`promote` to change
    state.
    """
    return replace(obj, version=obj.version + 1, **changes)


# ----- promotion gates (deterministic) ---------------------------------------

def promote(obj: FO, to_state: State, *, promoted_by: str,
            evidence_ref: str | None = None) -> FO:
    """Move an object along a legal edge, recording the evidence.

    Returns a *new* object (version bumped, state changed, lineage extended).
    Raises :class:`IllegalTransition` for any edge not in
    :data:`ALLOWED_TRANSITIONS`. This function does NOT itself judge whether the
    gate's criteria are met — callers consult :func:`eval_gate_passed` /
    :func:`risk_gate_passed` first; this just enforces the graph + records it.
    """
    if to_state not in ALLOWED_TRANSITIONS.get(obj.state, set()):
        raise IllegalTransition(f"{obj.type} {obj.id}: {obj.state} ↛ {to_state}")
    event = PromotionEvent(from_state=obj.state, to_state=to_state,
                           promoted_by=promoted_by, evidence_ref=evidence_ref)
    new_lineage = replace(obj.lineage, events=obj.lineage.events + (event,))
    return replace(obj, version=obj.version + 1, state=to_state, lineage=new_lineage)


def eval_gate_passed(ev: EvalSummary | None, *, min_n: int = 30,
                     max_ece: float = 0.04) -> bool:
    """Gate 2 (Hypothesis → paper): a pure function, no LLM.

    Passes only when the edge is statistically real (``beats_market``: CI upper
    bound < 0), the sample is large enough, AND calibration is within tolerance.
    """
    if ev is None:
        return False
    return ev.beats_market and ev.n >= min_n and ev.ece < max_ece


def risk_gate_passed(*, paper_apy: float, max_drawdown: float, paper_n: int,
                     manual_approved: bool, min_apy: float = 0.12,
                     dd_limit: float = -0.08, min_n: int = 60) -> bool:
    """Gate 3 (paper → Live): risk thresholds AND an explicit human approval.

    Live is never opened automatically; ``manual_approved`` is a required term.
    (Wired into a Live mode later — kept here so the gate logic lives with the
    state machine it guards.)
    """
    return (paper_apy > min_apy and max_drawdown > dd_limit
            and paper_n >= min_n and manual_approved)
