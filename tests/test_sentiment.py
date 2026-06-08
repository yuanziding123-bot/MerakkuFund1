"""Tests for FinGPT-inspired sentiment scoring."""
from __future__ import annotations

from polyagents.dataflows.sentiment import (
    LexiconSentimentScorer,
    SentimentScorer,
    aggregate_sentiment,
)


def test_polarity_direction():
    s = LexiconSentimentScorer()
    assert s.score("Stocks surge as company beats earnings and gains support") > 0.3
    assert s.score("Market plunge: crash, heavy losses and default fears") < -0.3
    assert s.score("The committee met on Tuesday afternoon") == 0.0


def test_score_bounded():
    s = LexiconSentimentScorer()
    for text in ["surge surge surge", "crash crash", "", "mixed surge and crash"]:
        assert -1.0 <= s.score(text) <= 1.0


def test_aggregate_mean_and_label():
    s = LexiconSentimentScorer()
    agg = aggregate_sentiment(
        ["Huge rally and record gains", "New deal approved, strong growth"], s
    )
    assert agg["n_scored"] == 2
    assert agg["mean"] > 0
    assert agg["label"] == "bullish"

    empty = aggregate_sentiment([], s)
    assert empty["n_scored"] == 0
    assert empty["label"] == "neutral"


def test_lexicon_scorer_satisfies_protocol():
    assert isinstance(LexiconSentimentScorer(), SentimentScorer)
