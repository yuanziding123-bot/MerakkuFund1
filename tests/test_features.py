"""Tests for Alpha DevBox-inspired feature extraction + Kronos forecaster hook."""
from __future__ import annotations

from polyagents.dataflows.features import extract_features
from polyagents.dataflows.forecaster import NullForecaster


def _raw() -> dict:
    return {
        "price": {"last_price": 0.5, "high": 0.6, "low": 0.4, "pct_change": 0.1, "closes": [0.4, 0.5]},
        "volume": {"total_volume": 1000.0, "recent_5bar_volume": 50.0, "baseline_avg_volume": 5.0},
        "orderbook": {"book_pressure": 0.2, "spread_bps": 100.0, "micro_price": 0.46, "mid": 0.45},
        "trades_flow": {"flow_imbalance": 0.3, "n_trades": 10},
        "news": {"sentiment": {"mean": 0.25}},
    }


def test_factor_vector_assembled():
    data = extract_features(_raw())
    f = data["factors"]
    assert f["price_momentum"] == 0.1
    assert round(f["price_range"], 4) == 0.4
    assert f["volume_spike_ratio"] == 2.0          # 50/5/5
    assert f["book_pressure"] == 0.2
    assert round(f["micro_vs_mid"], 4) == 0.01
    assert f["flow_imbalance"] == 0.3
    assert f["sentiment"] == 0.25
    # vector and names line up
    assert len(data["vector"]) == len(data["names"]) == len(f)


def test_missing_sources_default_to_zero():
    data = extract_features({})            # nothing collected
    assert all(v == 0.0 for v in data["vector"])
    assert "forecast" not in data          # NullForecaster adds nothing


def test_forecaster_hook_injects_forecast():
    class FakeForecaster:
        def forecast(self, closes):
            return {"direction": 1, "expected_move": 0.03} if closes else None

    data = extract_features(_raw(), forecaster=FakeForecaster())
    assert data["forecast"]["direction"] == 1

    # default null forecaster -> no forecast block
    assert "forecast" not in extract_features(_raw(), forecaster=NullForecaster())
