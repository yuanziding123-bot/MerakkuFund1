"""Tests for the evaluation subsystem (calibration / skill vs market baseline)."""
from __future__ import annotations

from pytest import approx

from polyagents.evaluation.evaluate import categorize, evaluate, format_report
from polyagents.evaluation.metrics import brier_score, ece, log_loss


def test_brier_and_log_loss():
    assert brier_score([1.0, 0.0], [1.0, 0.0]) == 0.0
    assert brier_score([0.5, 0.5], [1.0, 0.0]) == approx(0.25)
    assert log_loss([0.9], [1.0]) < log_loss([0.1], [1.0])    # confident-right < confident-wrong


def test_ece_perfect_and_bad():
    assert ece([0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]) == approx(0.0)
    assert ece([0.9, 0.9], [0.0, 0.0]) == approx(0.9)         # always says 0.9, always 0


def test_categorize():
    assert categorize("Will the President win the election?") == "politics"
    assert categorize("Will Bitcoin hit 100k by 2026?") == "crypto"
    assert categorize("Spurs vs. Knicks") == "sports"
    assert categorize("Will the Fed cut rates?") == "economy"
    assert categorize("random question") == "other"


def _rec(won, p, mkt, q):
    return {"status": "resolved", "won": won, "p_true": p, "raw_p_true": p,
            "market_price": mkt, "question": q}


def test_model_beats_market_significantly_has_ci():
    # 20 perfectly-informed predictions vs a 0.5 market -> CI excludes 0
    recs = [_rec(i % 2 == 0, 0.95 if i % 2 == 0 else 0.05, 0.5, f"election {i}")
            for i in range(20)]
    res = evaluate(recs)
    o = res["overall"]
    assert o["beats_market"] is True and o["beats_market_ci"] is True
    assert o["brier_delta"] < 0 and o["brier_delta_ci"][1] < 0     # whole CI below 0
    report = format_report(res)
    assert "BEATS market" in report and "95% CI" in report and "delta" in report


def test_model_loses_to_market():
    recs = [_rec(i % 2 == 0, 0.05 if i % 2 == 0 else 0.95, 0.5, f"q{i}")
            for i in range(20)]                                    # anti-correlated
    res = evaluate(recs)
    assert res["overall"]["beats_market"] is False
    assert "does NOT beat market" in format_report(res)


def _full_overall(**over):
    base = {"n": 5, "hit_rate": 0.5, "model_brier": 0.20, "raw_brier": 0.20,
            "market_brier": 0.22, "brier_skill_vs_market": 0.09, "beats_market": True,
            "brier_delta": -0.02, "brier_delta_ci": (-0.05, 0.01), "beats_market_ci": False,
            "sample_adequate": False, "model_log_loss": 0.6, "market_log_loss": 0.62,
            "model_ece": 0.04}
    base.update(over)
    return {"overall": base, "by_category": {}}


def test_verdict_tiers_and_sample_flag():
    # directional but CI includes 0 -> NOT significant; small n -> preliminary flag
    r = format_report(_full_overall(beats_market=True, beats_market_ci=False, sample_adequate=False))
    assert "NOT significant" in r and "preliminary" in r
    # CI excludes 0 -> significant BEATS
    assert "BEATS market" in format_report(
        _full_overall(beats_market=True, beats_market_ci=True, brier_delta_ci=(-0.05, -0.01)))
    # no edge
    assert "does NOT beat market" in format_report(
        _full_overall(beats_market=False, beats_market_ci=False))


def test_evaluate_empty():
    assert evaluate([])["n"] == 0
    assert "No resolved trades" in format_report(evaluate([]))
