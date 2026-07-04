"""Deterministic Lab backtest strategy registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


DEFAULT_STRATEGY_ID = "linear-factor-v1"


def _clip_probability(value: float) -> float:
    return min(0.99, max(0.01, float(value)))


def _factors(raw: dict) -> dict:
    return ((raw.get("features") or {}).get("factors") or {})


@dataclass(frozen=True)
class LabStrategy:
    id: str
    description: str
    source: str
    baseline: str
    score: Callable[[dict, float], dict]

    def predict(self, raw: dict, p_market: float) -> dict:
        output = self.score(raw, p_market)
        return {
            "id": self.id,
            "description": self.description,
            "source": self.source,
            "baseline": self.baseline,
            "p_market": float(p_market),
            **output,
        }


def _linear_factor_score(raw: dict, p_market: float) -> dict:
    weights = {
        "sentiment": 0.18,
        "flow_imbalance": 0.12,
        "book_pressure": 0.08,
        "spread_bps": -0.0005,
        "price_momentum": 0.08,
    }
    factors = _factors(raw)
    feature_vector = {name: float(factors.get(name, 0.0) or 0.0) for name in weights}
    contributions = {
        name: feature_vector[name] * float(weight)
        for name, weight in weights.items()
    }
    score_delta = sum(contributions.values())
    return {
        "p_raw": _clip_probability(p_market + score_delta),
        "score_delta": score_delta,
        "feature_vector": feature_vector,
        "feature_contributions": contributions,
        "weights": weights,
    }


def _market_naive_score(raw: dict, p_market: float) -> dict:
    return {
        "p_raw": _clip_probability(p_market),
        "score_delta": 0.0,
        "feature_vector": {},
        "feature_contributions": {},
        "weights": {},
    }


def _momentum_score(raw: dict, p_market: float) -> dict:
    weights = {
        "price_momentum": 0.28,
        "flow_imbalance": 0.06,
    }
    factors = _factors(raw)
    feature_vector = {name: float(factors.get(name, 0.0) or 0.0) for name in weights}
    contributions = {
        name: feature_vector[name] * float(weight)
        for name, weight in weights.items()
    }
    score_delta = sum(contributions.values())
    return {
        "p_raw": _clip_probability(p_market + score_delta),
        "score_delta": score_delta,
        "feature_vector": feature_vector,
        "feature_contributions": contributions,
        "weights": weights,
    }


STRATEGIES: dict[str, LabStrategy] = {
    "market-naive-v1": LabStrategy(
        id="market-naive-v1",
        description="Baseline strategy that trusts the historical market price.",
        source="market_baseline",
        baseline="market_price",
        score=_market_naive_score,
    ),
    "linear-factor-v1": LabStrategy(
        id="linear-factor-v1",
        description="Deterministic linear factor model over stored collection snapshots.",
        source="deterministic_factor_model",
        baseline="market_price",
        score=_linear_factor_score,
    ),
    "momentum-v1": LabStrategy(
        id="momentum-v1",
        description="Price-momentum strategy with light flow confirmation.",
        source="deterministic_momentum_model",
        baseline="market_price",
        score=_momentum_score,
    ),
}


def get_strategy(strategy_id: str | None = None) -> LabStrategy:
    selected = strategy_id or DEFAULT_STRATEGY_ID
    try:
        return STRATEGIES[selected]
    except KeyError as exc:
        allowed = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"unknown strategy_id '{selected}', expected one of: {allowed}") from exc
