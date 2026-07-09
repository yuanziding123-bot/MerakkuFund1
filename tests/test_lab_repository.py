"""Tests for the Lab SQLite repository."""
from __future__ import annotations

from polyagents.lab.backtest import BacktestRunner
from polyagents.lab.repository import LabRepository
from polyagents.lab.schemas import BacktestRequest, CreateHypothesisRequest
from polyagents.lab.service import create_hypothesis, get_hypothesis, list_hypotheses
from polyagents.storage.db import DataStore


def _request() -> CreateHypothesisRequest:
    return CreateHypothesisRequest(
        statement="Crypto news markets update slower than the model",
        category_filter="crypto",
        feature_set=["news_sentiment"],
        prompt_version="signal-v1",
        model_version="claude-sonnet-4",
        lineage={"source": "manual", "parents": []},
    )


def _backtest_request(hypothesis_id: str) -> BacktestRequest:
    return BacktestRequest(
        hypothesis_id=hypothesis_id,
        time_window={
            "start": "2026-03-01T00:00:00Z",
            "end": "2026-06-01T00:00:00Z",
        },
        market_filter={"category": "crypto", "settled_only": True},
        model_version="claude-sonnet-4",
        prompt_version="signal-v1",
        calibrator_id="shrink-to-market-v1",
    )


def test_repository_persists_hypotheses(tmp_path):
    repo = LabRepository(tmp_path / "lab.db")
    created = create_hypothesis(_request(), repo=repo)

    assert repo.counts()["objects"] == 1
    assert list_hypotheses(repo=repo)[0]["id"] == created.id
    assert get_hypothesis(created.id, repo=repo).statement.startswith("Crypto news")
    repo.close()


def test_backtest_runner_persists_evidence(tmp_path):
    repo = LabRepository(tmp_path / "lab.db")
    created = create_hypothesis(_request(), repo=repo)

    result = BacktestRunner(repo=repo).run(_backtest_request(created.id))

    counts = repo.counts()
    assert counts["forecasts"] == result.forecast_count
    assert counts["evaluations"] == 1
    assert counts["backtest_runs"] == 1
    assert repo.get_backtest_run(result.id).report_id == result.report_id
    report = repo.get_report(result.report_id)
    assert report["metrics"]["n"] == result.forecast_count
    assert report["time_window"]["start"] == "2026-03-01T00:00:00Z"
    assert report["backtest_config"]["calibrator_id"] == "shrink-to-market-v1"
    assert report["market_universe"]["source"] == "fixture"
    assert report["data_quality"]["uses_fixture_data"] is True
    assert report["scorecard"]["model_log_loss"] >= 0
    assert "calibration_bins" in report["scorecard"]
    assert len(report["market_sample"]) == result.forecast_count
    assert {"brier_model", "brier_market", "brier_delta"} <= set(report["market_sample"][0])
    assert report["backtest_config"]["signal_model_id"] == "linear-factor-v1"
    assert report["market_sample"][0]["signal_model"]["id"] == "fixture-v1"
    assert report["market_sample"][0]["snapshot_manifest"]["pit_status"] == "clean"
    assert report["pit_warnings"] == []
    repo.close()


def test_backtest_runner_uses_stored_collections(tmp_path):
    repo = LabRepository(tmp_path / "lab.db")
    store = DataStore(tmp_path / "data.db")
    created = create_hypothesis(_request(), repo=repo)
    store.record_collection(
        "token_yes_crypto_1",
        "2026-04-01T00:00:00Z",
        "Will bitcoin close above 100k?",
        0.55,
        {
            "features": {
                "factors": {
                    "sentiment": 0.4,
                    "flow_imbalance": 0.2,
                    "book_pressure": 0.1,
                    "spread_bps": 50,
                    "price_momentum": 0.05,
                }
            },
            "news": {
                "source": "historical_news",
                "n_items": 1,
                "sentiment": {"mean": 0.4, "label": "bullish"},
                "available_at": "2026-04-01T00:00:00Z",
                "skipped_no_published": 2,
                "skipped_future": 1,
                "items": [
                    {
                        "title": "Bitcoin bullish rally confirmed",
                        "url": "https://example.com/news",
                        "published": "2026-04-01T00:00:00Z",
                        "available_at": "2026-04-01T00:00:00Z",
                        "sentiment": 0.4,
                    }
                ],
            },
            "lab": {"outcome": 1, "available_at_max": "2026-04-01T00:00:00Z"},
        },
    )
    store.record_collection(
        "token_yes_crypto_2",
        "2026-04-02T00:00:00Z",
        "Will ethereum close above 5k?",
        0.45,
        {
            "features": {
                "factors": {
                    "sentiment": -0.4,
                    "flow_imbalance": -0.2,
                    "book_pressure": -0.1,
                    "spread_bps": 60,
                    "price_momentum": -0.05,
                }
            },
            "lab": {"outcome": 0, "available_at_max": "2026-04-02T00:00:00Z"},
        },
    )

    result = BacktestRunner(store=store, repo=repo).run(_backtest_request(created.id))

    forecasts = repo.forecasts_for_hypothesis(created.id)
    assert result.forecast_count == 2
    assert {f["market_token_id"] for f in forecasts} == {"token_yes_crypto_1", "token_yes_crypto_2"}
    assert forecasts[0]["p_cal"] != forecasts[0]["p_market"]
    report = repo.get_report(result.report_id)
    assert report["metrics"]["n"] == 2
    assert report["market_universe"]["source"] == "collections"
    assert report["market_universe"]["eligible_markets"] == 2
    assert report["data_quality"]["pit_clean"] is True
    assert {m["market_token_id"] for m in report["market_sample"]} == {
        "token_yes_crypto_1",
        "token_yes_crypto_2",
    }
    assert all(m["source"] == "collections" for m in report["market_sample"])
    sample = report["market_sample"][0]
    assert sample["signal_model"]["id"] == "linear-factor-v1"
    assert "sentiment" in sample["signal_model"]["feature_vector"]
    assert "sentiment" in sample["signal_model"]["feature_contributions"]
    assert sample["snapshot_manifest"]["prediction_time"] == "2026-04-01T00:00:00Z"
    assert sample["snapshot_manifest"]["sources"]
    assert sample["news_evidence"]["source"] == "historical_news"
    assert sample["news_evidence"]["n_items"] == 1
    assert sample["news_evidence"]["skipped_future"] == 1
    assert sample["news_evidence"]["items"][0]["title"] == "Bitcoin bullish rally confirmed"
    store.close()
    repo.close()


def test_backtest_runner_supports_strategy_registry(tmp_path):
    repo = LabRepository(tmp_path / "lab.db")
    store = DataStore(tmp_path / "data.db")
    created = create_hypothesis(_request(), repo=repo)
    store.record_collection(
        "token_yes_crypto_strategy",
        "2026-04-01T00:00:00Z",
        "Will bitcoin close above 100k?",
        0.50,
        {
            "features": {
                "factors": {
                    "sentiment": 0.4,
                    "flow_imbalance": 0.2,
                    "book_pressure": 0.1,
                    "spread_bps": 20,
                    "price_momentum": 0.3,
                }
            },
            "lab": {"outcome": 1, "available_at_max": "2026-04-01T00:00:00Z"},
        },
    )
    naive_request = BacktestRequest(
        hypothesis_id=created.id,
        time_window={
            "start": "2026-03-01T00:00:00Z",
            "end": "2026-06-01T00:00:00Z",
        },
        market_filter={"category": "crypto", "settled_only": True},
        model_version="claude-sonnet-4",
        prompt_version="signal-v1",
        calibrator_id="shrink-to-market-v1",
        strategy_id="market-naive-v1",
    )
    momentum_request = BacktestRequest(
        hypothesis_id=created.id,
        time_window={
            "start": "2026-03-01T00:00:00Z",
            "end": "2026-06-01T00:00:00Z",
        },
        market_filter={"category": "crypto", "settled_only": True},
        model_version="claude-sonnet-4",
        prompt_version="signal-v1",
        calibrator_id="shrink-to-market-v1",
        strategy_id="momentum-v1",
    )

    naive = BacktestRunner(store=store, repo=repo).run(naive_request)
    momentum = BacktestRunner(store=store, repo=repo).run(momentum_request)

    naive_report = repo.get_report(naive.report_id)
    momentum_report = repo.get_report(momentum.report_id)
    assert naive_report["backtest_config"]["strategy_id"] == "market-naive-v1"
    assert naive_report["strategy"]["baseline"] == "market_price"
    assert naive_report["market_sample"][0]["signal_model"]["id"] == "market-naive-v1"
    assert naive_report["market_sample"][0]["p_raw"] == naive_report["market_sample"][0]["p_market"]
    assert momentum_report["backtest_config"]["strategy_id"] == "momentum-v1"
    assert momentum_report["market_sample"][0]["signal_model"]["id"] == "momentum-v1"
    assert momentum_report["market_sample"][0]["p_raw"] != naive_report["market_sample"][0]["p_raw"]
    assert "price_momentum" in momentum_report["market_sample"][0]["signal_model"]["feature_vector"]
    assert "price_momentum" in momentum_report["market_sample"][0]["signal_model"]["feature_contributions"]
    store.close()
    repo.close()


def test_backtest_report_surfaces_pit_warnings_when_non_strict(tmp_path):
    repo = LabRepository(tmp_path / "lab.db")
    store = DataStore(tmp_path / "data.db")
    created = create_hypothesis(_request(), repo=repo)
    store.record_collection(
        "token_yes_crypto_future",
        "2026-04-01T00:00:00Z",
        "Will bitcoin close above 100k?",
        0.55,
        {
            "features": {"factors": {"sentiment": 0.4}},
            "lab": {
                "outcome": 1,
                "available_at_max": "2026-04-02T00:00:00Z",
            },
        },
    )
    request = BacktestRequest(
        hypothesis_id=created.id,
        time_window={
            "start": "2026-03-01T00:00:00Z",
            "end": "2026-06-01T00:00:00Z",
        },
        market_filter={"category": "crypto", "settled_only": True},
        model_version="claude-sonnet-4",
        prompt_version="signal-v1",
        calibrator_id="shrink-to-market-v1",
        pit_strict=False,
    )

    result = BacktestRunner(store=store, repo=repo).run(request)

    report = repo.get_report(result.report_id)
    assert result.forecast_count == 1
    assert report["market_universe"]["eligible_markets"] == 1
    assert report["data_quality"]["pit_clean"] is False
    assert report["data_quality"]["pit_warning_count"] == 1
    assert report["pit_warnings"][0]["market_token_id"] == "token_yes_crypto_future"
    assert report["market_sample"][0]["snapshot_manifest"]["pit_status"] == "warning"
    store.close()
    repo.close()
