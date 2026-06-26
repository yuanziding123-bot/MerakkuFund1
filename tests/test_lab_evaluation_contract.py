"""Contract tests for Lab probability evaluation summaries."""
from __future__ import annotations

import pytest


def test_evaluation_summary_uses_market_minus_model_brier_delta():
    from polyagents.evaluation.report import build_evaluation_summary

    summary = build_evaluation_summary(
        p_cal=[0.8, 0.7, 0.2, 0.1],
        p_market=[0.6, 0.55, 0.45, 0.4],
        outcomes=[1, 1, 0, 0],
        min_samples=4,
    )

    assert summary.brier_delta == pytest.approx(summary.brier_market - summary.brier_model)
    assert summary.brier_delta > 0
    assert summary.sample_adequate is True


def test_beats_market_requires_positive_ci_lower_bound():
    from polyagents.evaluation.report import EvalSummary

    summary = EvalSummary(
        n=100,
        brier_model=0.14,
        brier_market=0.16,
        brier_delta=0.02,
        brier_delta_ci=(-0.01, 0.05),
        ece=0.03,
        beats_market=True,
        sample_adequate=True,
        pit_clean=True,
    )

    assert summary.beats_market is False


def test_evaluation_report_gate_keeps_sample_size_separate_from_performance():
    from polyagents.evaluation.report import EvalSummary, promotion_gates

    summary = EvalSummary(
        n=12,
        brier_model=0.12,
        brier_market=0.18,
        brier_delta=0.06,
        brier_delta_ci=(0.02, 0.10),
        ece=0.02,
        beats_market=True,
        sample_adequate=False,
        pit_clean=True,
    )

    gates = promotion_gates(summary, ece_threshold=0.05)

    assert gates["beats_market"] is True
    assert gates["sample_adequate"] is False
    assert gates["paper_ready"] is False
