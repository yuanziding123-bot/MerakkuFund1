"""The 5 financial objects + state machine for AIHF v0.2.

This module owns immutable financial objects, deterministic promotion gates, and
small compatibility helpers used by earlier Lab tests.
"""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal

ObjectType = Literal["market", "hypothesis", "strategy", "position", "portfolio"]
State = Literal["draft", "lab", "paper", "live", "archived"]
ObjectState = State
PromotionRecommendation = Literal["remain_draft", "promote_to_lab", "promote_to_paper"]

ALLOWED_TRANSITIONS: dict[State, set[State]] = {
    "draft": {"lab", "archived"},
    "lab": {"paper", "archived"},
    "paper": {"live", "archived"},
    "live": {"archived"},
    "archived": set(),
}
VALID_STATES: set[str] = set(ALLOWED_TRANSITIONS)


class IllegalTransition(ValueError):
    """Raised when a promotion is not a legal edge in the state machine."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    return _now()


def new_id(object_type: ObjectType) -> str:
    prefix = {"market": "M", "hypothesis": "H", "strategy": "S",
              "position": "P", "portfolio": "PF"}[object_type]
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class EvalSummary:
    n: int
    brier_model: float
    brier_market: float
    brier_delta: float
    brier_delta_ci: tuple[float, float]
    ece: float
    beats_market: bool
    sample_adequate: bool


@dataclass(frozen=True)
class PromotionEvent:
    from_state: State
    to_state: State
    promoted_by: str
    evidence_ref: str | None = None
    promoted_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class Lineage:
    parent_id: str | None = None
    events: tuple[PromotionEvent, ...] = ()
    # Compatibility with the earlier lightweight object contract.
    parents: list[str] = field(default_factory=list)
    promoted_from: str | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True)
class FO:
    id: str
    type: ObjectType
    version: int
    snapshot_id: str
    state: State
    owner: str
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
    statement: str = ""
    category_filter: str = ""
    feature_set: tuple[str, ...] = ()
    prompt_version: str = ""
    model_version: str = ""


@dataclass(frozen=True)
class Strategy(FO):
    hypothesis_id: str = ""
    calibrator_id: str = ""
    sizing_rule: dict = field(default_factory=dict)
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
    positions: tuple[str, ...] = ()
    nav_usdc: float = 0.0


_INITIAL_STATE: dict[ObjectType, State] = {
    "market": "draft",
    "hypothesis": "draft",
    "strategy": "lab",
    "position": "paper",
    "portfolio": "live",
}

_CLASSES: dict[ObjectType, type[FO]] = {
    "market": Market,
    "hypothesis": Hypothesis,
    "strategy": Strategy,
    "position": Position,
    "portfolio": Portfolio,
}


def make(
    object_type: ObjectType,
    *,
    snapshot_id: str,
    owner: str = "default",
    parent_id: str | None = None,
    state: State | None = None,
    **fields,
) -> FO:
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
    return replace(obj, version=obj.version + 1, **changes)


def promote(obj: FO, to_state: State, *, promoted_by: str, evidence_ref: str | None = None) -> FO:
    if to_state not in ALLOWED_TRANSITIONS.get(obj.state, set()):
        raise IllegalTransition(f"{obj.type} {obj.id}: {obj.state} -> {to_state}")
    event = PromotionEvent(
        from_state=obj.state,
        to_state=to_state,
        promoted_by=promoted_by,
        evidence_ref=evidence_ref,
    )
    new_lineage = replace(obj.lineage, events=obj.lineage.events + (event,))
    return replace(obj, version=obj.version + 1, state=to_state, lineage=new_lineage)


def eval_gate_passed(ev: EvalSummary | None, *, min_n: int = 30, max_ece: float = 0.04) -> bool:
    if ev is None:
        return False
    return ev.beats_market and ev.n >= min_n and ev.ece < max_ece


def risk_gate_passed(
    *,
    paper_apy: float,
    max_drawdown: float,
    paper_n: int,
    manual_approved: bool,
    min_apy: float = 0.12,
    dd_limit: float = -0.08,
    min_n: int = 60,
) -> bool:
    return paper_apy > min_apy and max_drawdown > dd_limit and paper_n >= min_n and manual_approved


_TUPLE_FIELDS = {"feature_set", "positions"}


def to_dict(fo: FO) -> dict:
    return dataclasses.asdict(fo)


def from_dict(d: dict) -> FO:
    d = dict(d)
    lin = d.get("lineage") or {}
    events = tuple(PromotionEvent(**e) for e in (lin.get("events") or ()))
    d["lineage"] = Lineage(
        parent_id=lin.get("parent_id"),
        events=events,
        parents=lin.get("parents") or [],
        promoted_from=lin.get("promoted_from"),
        evidence_ref=lin.get("evidence_ref"),
    )
    ev = d.get("eval_summary")
    if ev:
        ev = dict(ev)
        ci = ev.get("brier_delta_ci")
        if ci is not None:
            ev["brier_delta_ci"] = tuple(ci)
        d["eval_summary"] = EvalSummary(**ev)
    for field_name in _TUPLE_FIELDS:
        if field_name in d and d[field_name] is not None:
            d[field_name] = tuple(d[field_name])
    return _CLASSES[d["type"]](**d)


def next_states(fo: FO) -> set[State]:
    return set(ALLOWED_TRANSITIONS.get(fo.state, set()))


# ----- Compatibility helpers -------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class FinancialObject:
    id: str
    type: ObjectType
    version: int
    state: ObjectState
    snapshotId: str
    lineage: Lineage
    createdAt: str

    def __post_init__(self) -> None:
        if self.state not in VALID_STATES:
            raise ValueError(f"invalid object state: {self.state}")
        if self.version < 1:
            raise ValueError("version must be >= 1")


@dataclass(frozen=True, kw_only=True)
class EvaluationReport:
    id: str
    type: Literal["evaluation_report"]
    version: int
    state: ObjectState
    snapshotId: str
    lineage: Lineage
    createdAt: str
    hypothesisId: str
    strategyId: str
    inputQuery: str
    parameters: dict[str, Any]
    marketCount: int
    tradeCount: int
    totalPnl: float
    winRate: float
    maxDrawdown: float
    sharpe: float
    profitFactor: float
    riskRating: Literal["Low", "Medium", "High"]
    caveats: list[str]
    promotionRecommendation: PromotionRecommendation

    def __post_init__(self) -> None:
        if self.state not in VALID_STATES:
            raise ValueError(f"invalid evaluation state: {self.state}")
        if self.version < 1:
            raise ValueError("version must be >= 1")


def recommendPromotion(input: dict[str, Any]) -> PromotionRecommendation:
    sample_size = int(input.get("sampleSize", input.get("tradeCount", 0)) or 0)
    total_pnl = float(input.get("totalPnl", 0) or 0)
    max_drawdown = float(input.get("maxDrawdown", input.get("maxDrawdownPct", 1)) or 0)
    sharpe = float(input.get("sharpe", 0) or 0)
    profit_factor = float(input.get("profitFactor", 0) or 0)
    risk_rating = input.get("riskRating", "High")

    if sample_size < 30 or risk_rating == "High":
        return "remain_draft"
    if (
        sample_size >= 100
        and total_pnl > 0
        and risk_rating in {"Low", "Medium"}
        and max_drawdown <= 0.2
        and sharpe >= 1.0
        and profit_factor >= 1.2
    ):
        return "promote_to_paper"
    if total_pnl > 0 and risk_rating in {"Low", "Medium"}:
        return "promote_to_lab"
    return "remain_draft"


def buildEvaluationReport(
    *,
    id: str,
    snapshotId: str,
    hypothesisId: str,
    strategyId: str,
    inputQuery: str,
    parameters: dict[str, Any],
    marketCount: int,
    tradeCount: int,
    totalPnl: float,
    winRate: float,
    maxDrawdown: float,
    sharpe: float,
    profitFactor: float,
    riskRating: Literal["Low", "Medium", "High"],
    caveats: list[str] | None = None,
) -> EvaluationReport:
    recommendation = recommendPromotion(
        {
            "sampleSize": tradeCount,
            "tradeCount": tradeCount,
            "totalPnl": totalPnl,
            "maxDrawdown": maxDrawdown,
            "sharpe": sharpe,
            "profitFactor": profitFactor,
            "riskRating": riskRating,
        }
    )
    return EvaluationReport(
        id=id,
        type="evaluation_report",
        version=1,
        state="lab",
        snapshotId=snapshotId,
        lineage=Lineage(parents=[hypothesisId, strategyId]),
        createdAt=now_iso(),
        hypothesisId=hypothesisId,
        strategyId=strategyId,
        inputQuery=inputQuery,
        parameters=parameters,
        marketCount=marketCount,
        tradeCount=tradeCount,
        totalPnl=totalPnl,
        winRate=winRate,
        maxDrawdown=maxDrawdown,
        sharpe=sharpe,
        profitFactor=profitFactor,
        riskRating=riskRating,
        caveats=caveats or [],
        promotionRecommendation=recommendation,
    )
