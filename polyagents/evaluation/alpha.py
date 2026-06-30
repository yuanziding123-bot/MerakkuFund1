"""Alpha test — the Lab's core job.

Answers the one question the whole system exists for: does this hypothesis's
calibrated probability *genuinely* beat the market baseline — not on a point
estimate, but with the lower/upper bound of a bootstrap CI on the Brier skill?

A prediction market is itself a strong, well-calibrated baseline. So an "alpha
test" scores the model's probabilities AND the market's on the same resolved
markets, takes the Brier delta (market − model; positive = the model is better),
and bootstraps a CI on that delta. The model only "beats the market" when the
WHOLE CI sits above zero. The result is an :class:`EvalSummary` — exactly what
the promotion gates read (gate 2 needs ``beats_market`` and enough samples).

Pure / deterministic (seeded bootstrap); no LLM, no network.
"""
from __future__ import annotations

import random

from polyagents.objects import EvalSummary

from .evaluate import categorize
from .metrics import brier_score, ece


def _resolved(records: list[dict]) -> list[dict]:
    return [r for r in records
            if r.get("status") == "resolved" and r.get("won") is not None
            and r.get("p_true") is not None and r.get("market_price") is not None]


def bootstrap_brier_delta_ci(model: list[float], market: list[float], y: list[float],
                             *, n_boot: int = 1000, seed: int = 0,
                             alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI on (Brier_market - Brier_model) by resampling markets."""
    n = len(y)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        m = [model[i] for i in idx]; mk = [market[i] for i in idx]; yy = [y[i] for i in idx]
        deltas.append(brier_score(mk, yy) - brier_score(m, yy))
    deltas.sort()
    lo = deltas[int((alpha / 2) * n_boot)]
    hi = deltas[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def alpha_test(records: list[dict], *, category: str | None = None,
               min_samples: int = 30, n_boot: int = 1000, seed: int = 0) -> EvalSummary:
    """Score resolved predictions in a slice vs the market and bootstrap the edge.

    ``category`` filters to one coarse category (politics / crypto / …) matching
    a hypothesis's ``category_filter``; ``None`` scores everything resolved.
    """
    recs = _resolved(records)
    if category:
        recs = [r for r in recs if categorize(r.get("question", "")) == category]
    n = len(recs)
    if n == 0:
        return EvalSummary(n=0, brier_model=0.0, brier_market=0.0, brier_delta=0.0,
                           brier_delta_ci=(0.0, 0.0), ece=0.0,
                           beats_market=False, sample_adequate=False)
    y = [1.0 if r.get("won") else 0.0 for r in recs]
    model = [float(r["p_true"]) for r in recs]
    market = [float(r["market_price"]) for r in recs]
    bm, bk = brier_score(model, y), brier_score(market, y)
    ci = bootstrap_brier_delta_ci(model, market, y, n_boot=n_boot, seed=seed)
    return EvalSummary(
        n=n, brier_model=bm, brier_market=bk, brier_delta=bk - bm,
        brier_delta_ci=ci, ece=ece(model, y),
        beats_market=ci[0] > 0,                 # whole CI above 0 -> model Brier lower = better
        sample_adequate=n >= min_samples,
    )
