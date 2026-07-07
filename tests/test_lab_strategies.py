"""Tests for Lab strategy registry."""
from __future__ import annotations

import pytest

from polyagents.lab.strategies import DEFAULT_STRATEGY_ID, STRATEGIES, get_strategy


def test_strategy_registry_default_and_allowed_ids():
    assert DEFAULT_STRATEGY_ID == "linear-factor-v1"
    assert set(STRATEGIES) == {
        "market-naive-v1",
        "linear-factor-v1",
        "momentum-v1",
        "flow-imbalance-v1",
        "microstructure-v1",
        "sentiment-v1",
        "contrarian-v1",
    }
    assert get_strategy().id == DEFAULT_STRATEGY_ID


def test_market_naive_strategy_trusts_market_price():
    strategy = get_strategy("market-naive-v1")
    output = strategy.predict({"features": {"factors": {"price_momentum": 0.9}}}, 0.42)

    assert output["p_raw"] == 0.42
    assert output["baseline"] == "market_price"
    assert output["feature_contributions"] == {}


def test_momentum_strategy_uses_momentum_features():
    strategy = get_strategy("momentum-v1")
    output = strategy.predict(
        {"features": {"factors": {"price_momentum": 0.5, "flow_imbalance": 0.2}}},
        0.40,
    )

    assert output["p_raw"] > 0.40
    assert "price_momentum" in output["feature_vector"]
    assert "price_momentum" in output["feature_contributions"]


def test_flow_imbalance_strategy_uses_trade_flow():
    strategy = get_strategy("flow-imbalance-v1")
    output = strategy.predict(
        {"features": {"factors": {"flow_imbalance": 0.5, "trade_count": 20}}},
        0.40,
    )

    assert output["p_raw"] > 0.40
    assert output["feature_vector"]["flow_imbalance"] == 0.5
    assert "trade_count" in output["feature_contributions"]


def test_contrarian_strategy_fades_positive_momentum():
    strategy = get_strategy("contrarian-v1")
    output = strategy.predict(
        {"features": {"factors": {"price_momentum": 0.5, "flow_imbalance": 0.2}}},
        0.60,
    )

    assert output["p_raw"] < 0.60
    assert output["feature_contributions"]["price_momentum"] < 0


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="unknown strategy_id"):
        get_strategy("missing-v1")
