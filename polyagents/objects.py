"""AIHF v0.2 financial objects and promotion recommendations.

This module is intentionally lightweight: it gives skills and reports a shared
object contract without introducing a database or changing the trading engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


ObjectType = Literal["market", "hypothesis", "strategy", "position", "portfolio"]
ObjectState = Literal["draft", "lab", "paper", "live", "archived"]
PromotionRecommendation = Literal[
    "remain_draft",
    "promote_to_lab",
    "promote_to_paper",
]

VALID_STATES: set[str] = {"draft", "lab", "paper", "live", "archived"}


@dataclass(frozen=True)
class Lineage:
    parents: list[str] = field(default_factory=list)
    promoted_from: str | None = None
    evidence_ref: str | None = None


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
class Market(FinancialObject):
    tokenId: str
    question: str
    metadata: dict[str, Any] = field(default_factory=dict)
    type: Literal["market"] = "market"


@dataclass(frozen=True, kw_only=True)
class Hypothesis(FinancialObject):
    statement: str
    type: Literal["hypothesis"] = "hypothesis"


@dataclass(frozen=True, kw_only=True)
class Strategy(FinancialObject):
    hypothesisId: str
    parameters: dict[str, Any] = field(default_factory=dict)
    type: Literal["strategy"] = "strategy"


@dataclass(frozen=True, kw_only=True)
class Position(FinancialObject):
    strategyId: str
    marketId: str
    side: str
    sizeUsdc: float
    type: Literal["position"] = "position"


@dataclass(frozen=True, kw_only=True)
class Portfolio(FinancialObject):
    positions: list[str] = field(default_factory=list)
    navUsdc: float = 0.0
    type: Literal["portfolio"] = "portfolio"


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def recommendPromotion(input: dict[str, Any]) -> PromotionRecommendation:
    """Return a deterministic recommendation; never mutates object state."""
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
