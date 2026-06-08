"""Deterministic risk + sizing for the decision agent.

The Merakku v3.0 plan folds risk into the decision agent ("风控嵌入决策"). We
keep it as pure, auditable math — fractional Kelly sizing plus hard gates —
rather than letting an LLM size positions. Constants mirror the polymarket
reference repo (6% edge floor, quarter Kelly, 5% position cap).
"""
from __future__ import annotations


def edge_for_side(p_true: float, market_price: float) -> float:
    """Edge from buying the analysed side: estimated prob minus its price.

    A YES/NO share costs ``market_price`` and pays $1 if the side resolves.
    Positive edge ⇒ underpriced ⇒ buy candidate; negative ⇒ overpriced.
    """
    return p_true - market_price


def kelly_fraction(p_true: float, market_price: float) -> float:
    """Full-Kelly stake fraction for a binary contract at ``market_price``.

    f* = (q - p) / (1 - p), clamped to [0, 1]. Zero when there's no positive
    edge or the price leaves no room (≈ 1.0).
    """
    denom = 1.0 - market_price
    if denom <= 1e-9:
        return 0.0
    f = (p_true - market_price) / denom
    return max(0.0, min(1.0, f))
