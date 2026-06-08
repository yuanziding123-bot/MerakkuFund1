"""News/headline sentiment — inspired by FinGPT.

FinGPT fine-tunes an LLM for financial sentiment. Pulling GPU/LoRA weights into
this layer would be heavy, so we define a small ``SentimentScorer`` protocol and
ship a deterministic lexicon scorer as the default. A real FinGPT- or
LLM-backed scorer can be dropped in later without touching the graph — the
news collector takes whatever scorer it's handed.

Scores are in ``[-1, 1]`` (negative → bearish, positive → bullish).
"""
from __future__ import annotations

import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class SentimentScorer(Protocol):
    def score(self, text: str) -> float:
        """Return a sentiment score in [-1, 1] for one piece of text."""
        ...


# Compact finance-flavoured polarity lexicon. Deliberately small and auditable;
# swap in FinGPT for nuance.
_POSITIVE = {
    "surge", "surges", "soar", "soars", "rally", "rallies", "gain", "gains", "rise",
    "rises", "jump", "jumps", "beat", "beats", "strong", "growth", "boost", "wins",
    "win", "approve", "approved", "support", "optimistic", "bullish", "record",
    "upgrade", "expand", "expands", "agreement", "deal", "ceasefire", "resolved",
    "success", "positive", "confirm", "confirmed", "leads", "leading",
}
_NEGATIVE = {
    "plunge", "plunges", "crash", "crashes", "fall", "falls", "drop", "drops",
    "loss", "losses", "miss", "misses", "weak", "decline", "declines", "cut",
    "cuts", "reject", "rejected", "fear", "fears", "bearish", "slump", "downgrade",
    "risk", "risks", "warning", "warn", "warns", "delay", "delayed", "fail",
    "fails", "collapse", "conflict", "escalate", "escalates", "negative", "concern",
    "concerns", "uncertain", "uncertainty", "default", "crisis", "ban", "banned",
}

_WORD_RE = re.compile(r"[a-z']+")


class LexiconSentimentScorer:
    """Deterministic, dependency-free polarity scorer (FinGPT stand-in)."""

    def __init__(self, positive: set[str] | None = None, negative: set[str] | None = None) -> None:
        self._pos = positive if positive is not None else _POSITIVE
        self._neg = negative if negative is not None else _NEGATIVE

    def score(self, text: str) -> float:
        if not text:
            return 0.0
        pos = neg = 0
        for w in _WORD_RE.findall(text.lower()):
            if w in self._pos:
                pos += 1
            elif w in self._neg:
                neg += 1
        total = pos + neg
        return (pos - neg) / total if total else 0.0


def _label(score: float) -> str:
    if score > 0.15:
        return "bullish"
    if score < -0.15:
        return "bearish"
    return "neutral"


def aggregate_sentiment(texts: list[str], scorer: SentimentScorer) -> dict:
    """Mean sentiment over a list of headlines/snippets."""
    scores = [scorer.score(t) for t in texts if t]
    if not scores:
        return {"n_scored": 0, "mean": 0.0, "label": "neutral", "scores": []}
    mean = sum(scores) / len(scores)
    return {
        "n_scored": len(scores),
        "mean": mean,
        "label": _label(mean),
        "scores": scores,
    }
