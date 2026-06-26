"""Tests for AIHF v0.2 object flow helpers."""
from __future__ import annotations

import pytest

from polyagents.objects import (
    FinancialObject,
    Lineage,
    buildEvaluationReport,
    recommendPromotion,
)


def test_financial_object_rejects_invalid_state():
    with pytest.raises(ValueError):
        FinancialObject(
            id="x",
            type="hypothesis",
            version=1,
            state="review",  # type: ignore[arg-type]
            snapshotId="snap-1",
            lineage=Lineage(),
            createdAt="2026-06-23T00:00:00Z",
        )


def test_evaluation_report_contains_required_fields():
    report = buildEvaluationReport(
        id="eval-1",
        snapshotId="snap-1",
        hypothesisId="hyp-1",
        strategyId="strat-1",
        inputQuery="bitcoin",
        parameters={"entryHigh": 0.9},
        marketCount=3,
        tradeCount=120,
        totalPnl=42.0,
        winRate=0.62,
        maxDrawdown=0.08,
        sharpe=1.6,
        profitFactor=1.7,
        riskRating="Low",
        caveats=["baseline only"],
    )

    assert report.hypothesisId == "hyp-1"
    assert report.strategyId == "strat-1"
    assert report.inputQuery == "bitcoin"
    assert report.parameters["entryHigh"] == 0.9
    assert report.marketCount == 3
    assert report.tradeCount == 120
    assert report.promotionRecommendation == "promote_to_paper"
    assert report.lineage.parents == ["hyp-1", "strat-1"]


def test_promotion_recommendation_covers_three_paths():
    assert (
        recommendPromotion(
            {
                "sampleSize": 5,
                "totalPnl": 100,
                "riskRating": "Low",
                "sharpe": 2,
                "profitFactor": 2,
                "maxDrawdown": 0.01,
            }
        )
        == "remain_draft"
    )
    assert (
        recommendPromotion(
            {
                "sampleSize": 40,
                "totalPnl": 20,
                "riskRating": "Medium",
                "sharpe": 0.7,
                "profitFactor": 1.1,
                "maxDrawdown": 0.12,
            }
        )
        == "promote_to_lab"
    )
    assert (
        recommendPromotion(
            {
                "sampleSize": 100,
                "totalPnl": 20,
                "riskRating": "Medium",
                "sharpe": 1.2,
                "profitFactor": 1.3,
                "maxDrawdown": 0.1,
            }
        )
        == "promote_to_paper"
    )


def test_high_risk_never_promotes_to_paper():
    assert (
        recommendPromotion(
            {
                "sampleSize": 300,
                "totalPnl": 200,
                "riskRating": "High",
                "sharpe": 4,
                "profitFactor": 4,
                "maxDrawdown": 0.01,
            }
        )
        != "promote_to_paper"
    )
