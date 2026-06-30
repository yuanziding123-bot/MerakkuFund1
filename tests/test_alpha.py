"""Alpha test — bootstrap-CI Brier skill vs the market baseline."""
from __future__ import annotations

from polyagents.evaluation.alpha import alpha_test, bootstrap_brier_delta_ci


def _rec(question, p_model, p_market, won):
    return {"status": "resolved", "won": won, "p_true": p_model,
            "market_price": p_market, "question": question}


def test_model_clearly_better_beats_market_with_ci_above_zero():
    # model nails every outcome (p→1 when won, →0 when lost); market hedges at 0.5
    recs = []
    for i in range(60):
        won = i % 2 == 0
        recs.append(_rec("crypto bitcoin market", 0.95 if won else 0.05, 0.5, won))
    ev = alpha_test(recs, n_boot=500, seed=1)
    assert ev.n == 60 and ev.sample_adequate
    assert ev.brier_model < ev.brier_market and ev.brier_delta > 0
    assert ev.brier_delta_ci[0] > 0          # whole CI above 0
    assert ev.beats_market is True


def test_model_equal_to_market_does_not_beat():
    recs = [_rec("politics election", 0.5, 0.5, i % 2 == 0) for i in range(40)]
    ev = alpha_test(recs, n_boot=500, seed=1)
    assert ev.brier_delta == 0.0
    assert ev.brier_delta_ci[0] <= 0 <= ev.brier_delta_ci[1]   # straddles 0
    assert ev.beats_market is False


def test_small_sample_flagged_inadequate():
    recs = [_rec("crypto eth", 0.9 if i % 2 else 0.1, 0.5, i % 2 == 1) for i in range(10)]
    ev = alpha_test(recs, n_boot=200, seed=1)
    assert ev.n == 10 and ev.sample_adequate is False


def test_category_filter_slices_records():
    recs = [_rec("bitcoin crypto market", 0.9, 0.5, True),
            _rec("senate election politics", 0.9, 0.5, True)]
    ev = alpha_test(recs, category="crypto", n_boot=100, seed=1)
    assert ev.n == 1


def test_empty_returns_zero_summary():
    ev = alpha_test([], n_boot=100)
    assert ev.n == 0 and ev.beats_market is False and ev.sample_adequate is False


def test_bootstrap_is_deterministic_with_seed():
    model = [0.9, 0.1, 0.8, 0.2] * 10
    market = [0.5] * 40
    y = [1.0, 0.0, 1.0, 0.0] * 10
    a = bootstrap_brier_delta_ci(model, market, y, n_boot=300, seed=7)
    b = bootstrap_brier_delta_ci(model, market, y, n_boot=300, seed=7)
    assert a == b and a[0] <= a[1]
