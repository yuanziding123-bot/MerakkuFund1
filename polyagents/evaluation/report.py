"""Evaluation reports for Lab probability research."""
from __future__ import annotations

import random
from dataclasses import dataclass

from .metrics import brier_score, calibration_curve, ece, log_loss


@dataclass(frozen=True, kw_only=True)
class EvalSummary:
    n: int
    brier_model: float
    brier_market: float
    brier_delta: float
    brier_delta_ci: tuple[float, float]
    ece: float
    beats_market: bool
    sample_adequate: bool
    pit_clean: bool = True

    def __post_init__(self) -> None:
        # The gate is deterministic: a positive point estimate is insufficient.
        object.__setattr__(self, "beats_market", bool(self.brier_delta_ci[0] > 0))


def bootstrap_ci(values: list[float], *, n_boot: int = 1000, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap CI for per-market Brier skill deltas.

    Values use the project-wide convention: ``market_brier - model_brier``.
    Positive means the model beat the market baseline.
    """
    if not values:
        return (float("nan"), float("nan"))
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (means[int(0.025 * n_boot)], means[min(n_boot - 1, int(0.975 * n_boot))])


def scorecard(*, p_cal: list[float], p_market: list[float], outcomes: list[float]) -> dict:
    """Detailed probability-quality metrics for EvaluationReport."""
    return {
        "model_log_loss": log_loss(p_cal, outcomes),
        "market_log_loss": log_loss(p_market, outcomes),
        "calibration_bins": calibration_curve(p_cal, outcomes),
        "market_calibration_bins": calibration_curve(p_market, outcomes),
    }


def build_evaluation_summary(
    *,
    p_cal: list[float],
    p_market: list[float],
    outcomes: list[float],
    min_samples: int = 30,
    pit_clean: bool = True,
) -> EvalSummary:
    if not (len(p_cal) == len(p_market) == len(outcomes)):
        raise ValueError("p_cal, p_market, and outcomes must have the same length")
    n = len(outcomes)
    model_brier = brier_score(p_cal, outcomes)
    market_brier = brier_score(p_market, outcomes)
    delta = market_brier - model_brier
    per_sample_delta = [
        (mkt - y) ** 2 - (model - y) ** 2
        for model, mkt, y in zip(p_cal, p_market, outcomes)
    ]
    ci = bootstrap_ci(per_sample_delta)
    return EvalSummary(
        n=n,
        brier_model=model_brier,
        brier_market=market_brier,
        brier_delta=delta,
        brier_delta_ci=ci,
        ece=ece(p_cal, outcomes),
        beats_market=ci[0] > 0,
        sample_adequate=n >= min_samples,
        pit_clean=pit_clean,
    )


def promotion_gates(summary: EvalSummary, *, ece_threshold: float = 0.05) -> dict[str, bool]:
    ece_pass = summary.ece <= ece_threshold
    paper_ready = (
        summary.sample_adequate
        and summary.beats_market
        and ece_pass
        and summary.pit_clean
    )
    return {
        "sample_adequate": summary.sample_adequate,
        "beats_market": summary.beats_market,
        "ece_pass": ece_pass,
        "pit_clean": summary.pit_clean,
        "paper_ready": paper_ready,
    }
