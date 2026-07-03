"""Evaluation subsystem — does our p_true actually have edge over the market?

The headline question (per the feedback): a prediction market is, in many
domains, a well-calibrated baseline. If our model's probabilities don't beat
"just trust the market price", the apparent edge is noise. So we score the
model's predictions AND the market's, on the same resolved trades, and compare.

Operates over the decision log (memory records), including HOLDs — every analysed
market with a known outcome is a data point (counterfactual logging), not just
the ones we traded. Stratified by a coarse keyword category.
"""
from __future__ import annotations

from .metrics import brier_score, calibration_curve, ece, log_loss

_CATEGORIES = {
    "politics": ["election", "president", "senate", "vote", "minister", "parliament", "govern", "poll"],
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "token", "coin", "solana"],
    "sports": ["win the", "fifa", "world cup", "nba", "match", "vs.", " vs ", "league", "cup", "open"],
    "economy": ["fed", "rate", "inflation", "gdp", "cpi", "recession", "jobs", "tariff"],
    "geopolitics": ["ceasefire", "war", "sanction", "airspace", "peace", "nuclear", "border"],
}


def categorize(question: str) -> str:
    q = (question or "").lower()
    for cat, kws in _CATEGORIES.items():
        if any(k in q for k in kws):
            return cat
    return "other"


def _score_group(records: list[dict]) -> dict:
    """Score one group of resolved records against the market baseline."""
    y = [1.0 if r.get("won") else 0.0 for r in records]
    model = [float(r.get("p_true")) for r in records]                 # calibrated p used to size
    raw = [float(r.get("raw_p_true") if r.get("raw_p_true") is not None else r.get("p_true")) for r in records]
    market = [float(r.get("market_price")) for r in records]
    model_brier, market_brier = brier_score(model, y), brier_score(market, y)
    # bootstrap CI on the Brier delta (model − market; negative = model better).
    # Lazy import to avoid the evaluate↔alpha module cycle.
    from .alpha import bootstrap_brier_delta_ci
    ci = bootstrap_brier_delta_ci(model, market, y) if len(y) >= 2 else (0.0, 0.0)
    return {
        "n": len(records),
        "hit_rate": sum(y) / len(y),
        "model_brier": model_brier,
        "raw_brier": brier_score(raw, y),
        "market_brier": market_brier,
        "brier_skill_vs_market": (1 - model_brier / market_brier) if market_brier else 0.0,
        "beats_market": model_brier < market_brier,           # point estimate
        "brier_delta": model_brier - market_brier,
        "brier_delta_ci": ci,                                  # bootstrap 95% CI
        "beats_market_ci": ci[1] < 0,                          # whole CI below 0 = significant
        "sample_adequate": len(records) >= 30,
        "model_log_loss": log_loss(model, y),
        "market_log_loss": log_loss(market, y),
        "model_ece": ece(model, y),
        "calibration_curve": calibration_curve(model, y),
    }


def evaluate(records: list[dict]) -> dict:
    """Overall + per-category scores over resolved records."""
    resolved = [
        r for r in records
        if r.get("status") == "resolved" and r.get("won") is not None
        and r.get("p_true") is not None and r.get("market_price") is not None
    ]
    if not resolved:
        return {"n": 0, "pending": sum(1 for r in records if r.get("status") == "pending")}
    by_cat: dict[str, list[dict]] = {}
    for r in resolved:
        by_cat.setdefault(categorize(r.get("question", "")), []).append(r)
    return {
        "overall": _score_group(resolved),
        "by_category": {cat: _score_group(rs) for cat, rs in sorted(by_cat.items())},
    }


def format_report(result: dict) -> str:
    if "overall" not in result:
        return f"No resolved trades to evaluate ({result.get('pending', 0)} pending)."
    o = result["overall"]
    if o["beats_market_ci"]:
        verdict = "BEATS market ✅ (bootstrap 95% CI excludes 0 — significant)"
    elif o["beats_market"]:
        verdict = "directionally ahead ⚠ (95% CI includes 0 — NOT significant)"
    else:
        verdict = "does NOT beat market ❌ (edge is likely noise)"
    lo, hi = o["brier_delta_ci"]
    n_note = "" if o["sample_adequate"] else "  ⚠ sample < 30, preliminary"
    lines = [
        f"Evaluation — {o['n']} resolved predictions{n_note}",
        f"  model {verdict}",
        f"  Brier: model {o['model_brier']:.3f} vs market {o['market_brier']:.3f}  "
        f"(delta {o['brier_delta']:+.3f}, bootstrap 95% CI [{lo:+.3f}, {hi:+.3f}])",
        f"  raw-model Brier {o['raw_brier']:.3f}  (calibration {'helped' if o['model_brier'] <= o['raw_brier'] else 'hurt'})",
        f"  log-loss: model {o['model_log_loss']:.3f} vs market {o['market_log_loss']:.3f}",
        f"  calibration error (ECE): {o['model_ece']:.3f}  |  hit rate {o['hit_rate']:.0%}",
        "  by category (n · Brier delta · 95% CI):",
    ]
    for cat, s in result["by_category"].items():
        clo, chi = s["brier_delta_ci"]
        flag = "✅" if s["beats_market_ci"] else ("⚠" if s["beats_market"] else "❌")
        adq = "" if s["sample_adequate"] else " (n<30)"
        lines.append(f"    {cat:12} n={s['n']:<3} delta {s['brier_delta']:+.3f} "
                     f"CI [{clo:+.3f}, {chi:+.3f}] {flag}{adq}")
    return "\n".join(lines)
