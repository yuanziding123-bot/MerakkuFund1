"""Minimal Lab backtest runner.

This first implementation is deterministic and local. It establishes the
contract for forecasts, reports, and PIT checks before the historical-market
replay is wired to real stored data.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict

from polyagents.evaluation.report import build_evaluation_summary, promotion_gates
from polyagents.evaluation.evaluate import categorize

from .pit import assert_point_in_time
from .repository import LabRepository
from .schemas import BacktestRequest, BacktestRunResult, ForecastRecord, utc_now
from .service import default_repository


def _short_hash(raw: str) -> str:
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


class BacktestRunner:
    def __init__(self, store=None, repo: LabRepository | None = None) -> None:
        if isinstance(store, LabRepository) and repo is None:
            repo = store
        elif store == ":memory:" and repo is None:
            repo = LabRepository(":memory:")
        self.store = store
        self.repo = repo or default_repository()

    def run(self, request: BacktestRequest) -> BacktestRunResult:
        started = utc_now()
        run_id = f"bt_{_short_hash(request.hypothesis_id + started)}"
        prediction_time = request.time_window["end"]
        forecasts = self._forecasts_from_store(request, run_id)
        if not forecasts:
            forecasts = self._fixture_forecasts(request, run_id, prediction_time)
        summary = build_evaluation_summary(
            p_cal=[f.p_cal for f in forecasts],
            p_market=[f.p_market for f in forecasts],
            outcomes=[float(f.outcome) for f in forecasts if f.outcome is not None],
            min_samples=30,
        )
        report_id = f"eval_{_short_hash(run_id + request.hypothesis_id)}"
        report = {
            "id": report_id,
            "type": "evaluation_report",
            "hypothesis_id": request.hypothesis_id,
            "backtest_run_id": run_id,
            "scope": f"hypothesis:{request.hypothesis_id}",
            "time_window": dict(request.time_window),
            "metrics": asdict(summary),
            "gates": promotion_gates(summary),
            "pit_warnings": [],
            "market_sample": [
                {
                    "market_token_id": f.market_token_id,
                    "p_cal": f.p_cal,
                    "p_market": f.p_market,
                    "outcome": f.outcome,
                }
                for f in forecasts
            ],
            "generated_at": utc_now(),
        }
        self.repo.save_forecasts(forecasts)
        self.repo.save_evaluation(report)
        result = BacktestRunResult(
            id=run_id,
            hypothesis_id=request.hypothesis_id,
            status="completed",
            report_id=report_id,
            forecast_count=len(forecasts),
            started_at=started,
            finished_at=utc_now(),
        )
        self.repo.save_backtest_run(result)
        return result

    def _forecasts_from_store(self, request: BacktestRequest, run_id: str) -> list[ForecastRecord]:
        if not hasattr(self.store, "fetch_collections"):
            return []
        rows = self.store.fetch_collections(
            request.time_window["start"],
            request.time_window["end"],
            limit=request.max_markets,
        )
        category = request.market_filter.get("category")
        forecasts: list[ForecastRecord] = []
        for row in rows:
            if category and category != "all" and categorize(row.get("question", "")) != category:
                continue
            raw = row.get("raw") or {}
            lab = raw.get("lab") or {}
            outcome = lab.get("outcome", raw.get("outcome"))
            if outcome is None:
                continue
            prediction_time = row.get("as_of") or request.time_window["end"]
            available_at_max = lab.get("available_at_max") or raw.get("available_at_max") or prediction_time
            assert_point_in_time(
                [{"feature": "collection", "available_at": available_at_max}],
                prediction_time,
                strict=request.pit_strict,
            )
            p_market = float(row.get("market_price") or lab.get("p_market") or 0.5)
            p_raw = float(lab.get("p_raw", self._score_collection(raw, p_market)))
            p_cal = float(lab.get("p_cal", self._calibrate_to_market(p_raw, p_market)))
            token = row["token_id"]
            forecasts.append(
                ForecastRecord(
                    id=f"fc_{_short_hash(run_id + token + prediction_time)}",
                    hypothesis_id=request.hypothesis_id,
                    market_token_id=token,
                    snapshot_id=f"snap_{_short_hash(token + prediction_time)}",
                    p_raw=p_raw,
                    p_cal=p_cal,
                    p_market=p_market,
                    outcome=int(outcome),
                    model_version=request.model_version,
                    prompt_version=request.prompt_version,
                    calibrator_id=request.calibrator_id,
                    prediction_time=prediction_time,
                    available_at_max=available_at_max,
                )
            )
        return forecasts

    def _fixture_forecasts(
        self,
        request: BacktestRequest,
        run_id: str,
        prediction_time: str,
    ) -> list[ForecastRecord]:
        feature_rows = [
            {"feature": "news_sentiment", "available_at": request.time_window["start"]},
            {"feature": "orderbook_imbalance", "available_at": request.time_window["start"]},
        ]
        assert_point_in_time(feature_rows, prediction_time, strict=request.pit_strict)
        fixtures = [
            ("token_yes_1", 0.72, 0.66, 0.54, 1),
            ("token_yes_2", 0.64, 0.60, 0.50, 1),
            ("token_yes_3", 0.28, 0.34, 0.46, 0),
            ("token_yes_4", 0.22, 0.30, 0.42, 0),
        ][: request.max_markets]
        return [
            ForecastRecord(
                id=f"fc_{_short_hash(run_id + token)}",
                hypothesis_id=request.hypothesis_id,
                market_token_id=token,
                snapshot_id=f"snap_{_short_hash(token + prediction_time)}",
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
                outcome=outcome,
                model_version=request.model_version,
                prompt_version=request.prompt_version,
                calibrator_id=request.calibrator_id,
                prediction_time=prediction_time,
                available_at_max=request.time_window["start"],
            )
            for token, p_raw, p_cal, p_market, outcome in fixtures
        ]

    @staticmethod
    def _calibrate_to_market(p_raw: float, p_market: float) -> float:
        return min(0.99, max(0.01, 0.7 * p_raw + 0.3 * p_market))

    @staticmethod
    def _score_collection(raw: dict, p_market: float) -> float:
        factors = ((raw.get("features") or {}).get("factors") or {})
        score = (
            0.18 * float(factors.get("sentiment", 0.0))
            + 0.12 * float(factors.get("flow_imbalance", 0.0))
            + 0.08 * float(factors.get("book_pressure", 0.0))
            - 0.0005 * float(factors.get("spread_bps", 0.0))
            + 0.08 * float(factors.get("price_momentum", 0.0))
        )
        return min(0.99, max(0.01, p_market + score))


def get_backtest_run(run_id: str) -> BacktestRunResult | None:
    return default_repository().get_backtest_run(run_id)


def get_report(report_id: str) -> dict | None:
    return default_repository().get_report(report_id)
