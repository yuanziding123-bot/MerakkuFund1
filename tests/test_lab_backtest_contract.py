"""Contract tests for the future Lab backtest runner."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


def test_backtest_request_requires_settled_markets_and_strict_pit():
    from polyagents.lab.schemas import BacktestRequest

    request = BacktestRequest(
        hypothesis_id="hyp_001",
        time_window={
            "start": "2026-03-01T00:00:00Z",
            "end": "2026-06-01T00:00:00Z",
        },
        market_filter={"category": "crypto", "settled_only": True},
        model_version="claude-sonnet-4",
        prompt_version="signal-v1",
        calibrator_id="shrink-to-market-v1",
    )

    assert request.pit_strict is True
    assert request.strategy_id == "linear-factor-v1"
    assert request.market_filter["settled_only"] is True


def test_point_in_time_assertion_rejects_future_features():
    from polyagents.lab.pit import assert_point_in_time

    prediction_time = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    feature_rows = [
        {"feature": "news_sentiment", "available_at": "2026-04-10T12:01:00Z"},
    ]

    with pytest.raises(ValueError, match="PIT"):
        assert_point_in_time(feature_rows, prediction_time, strict=True)


def test_backtest_runner_writes_forecasts_and_report():
    from polyagents.lab.backtest import BacktestRunner
    from polyagents.lab.schemas import BacktestRequest

    runner = BacktestRunner(store=":memory:")
    request = BacktestRequest(
        hypothesis_id="hyp_001",
        time_window={
            "start": "2026-03-01T00:00:00Z",
            "end": "2026-06-01T00:00:00Z",
        },
        market_filter={"category": "crypto", "settled_only": True},
        model_version="claude-sonnet-4",
        prompt_version="signal-v1",
        calibrator_id="shrink-to-market-v1",
    )

    result = runner.run(request)

    assert result.status == "completed"
    assert result.report_id.startswith("eval_")
    assert result.forecast_count > 0
