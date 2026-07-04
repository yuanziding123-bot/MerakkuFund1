"""Lab backtesting paths.

This module intentionally supports two complementary routes:

* ``replay()`` runs the price-history alpha replay from ``main``. It is a
  deterministic, point-in-time historical test over resolved markets.
* ``run()`` is the Lab MVP evidence path from PR #5. It turns a
  ``BacktestRequest`` into persisted forecasts, an EvaluationReport, and a
  BacktestRunResult.

Keeping both routes in one runner preserves the Ask/main work while allowing the
Lab API to persist hypothesis evidence.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable

from polyagents.evaluation.alpha import alpha_test
from polyagents.evaluation.evaluate import categorize
from polyagents.evaluation.report import build_evaluation_summary, promotion_gates, scorecard

from .pit import assert_point_in_time
from .repository import LabRepository
from .schemas import BacktestRequest, BacktestRunResult, ForecastRecord, utc_now
from .service import default_repository
from .strategies import DEFAULT_STRATEGY_ID, STRATEGIES, get_strategy


FACTOR_MODEL_V1 = {
    "id": DEFAULT_STRATEGY_ID,
    "description": "Deterministic factor model over stored collection snapshots.",
    "intercept": 0.0,
    "weights": {
        "sentiment": 0.18,
        "flow_imbalance": 0.12,
        "book_pressure": 0.08,
        "spread_bps": -0.0005,
        "price_momentum": 0.08,
    },
}


class PointInTimeError(AssertionError):
    """Raised if any feature used for a prediction post-dates prediction_time."""


def naive_signal(candles, market_price: float) -> float:
    """Trust the market; useful as a null-edge sanity check."""
    return market_price


def momentum_signal(candles, market_price: float) -> float:
    """Nudge the market price by recent trend."""
    closes = [c.close for c in candles]
    if len(closes) < 4:
        return market_price
    look = min(len(closes), 12)
    trend = closes[-1] - closes[-look]
    return max(0.02, min(0.98, market_price + 0.5 * trend))


def _short_hash(raw: str) -> str:
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


@dataclass
class BacktestRunner:
    """Historical replay runner plus Lab evidence runner."""

    client: object | None = None
    predict_frac: float = 0.5
    max_markets: int = 30
    min_history: int = 4
    signal_fn: Callable = field(default=momentum_signal)
    store: object | None = None
    repo: LabRepository | None = None

    def __post_init__(self) -> None:
        if isinstance(self.store, LabRepository) and self.repo is None:
            self.repo = self.store
            self.store = None
        elif self.store == ":memory:" and self.repo is None:
            self.repo = LabRepository(":memory:")
        if self.repo is None:
            self.repo = default_repository()

    # ----- historical replay -------------------------------------------------

    def replay(self, category: str | None = None, *, markets=None) -> dict:
        if self.client is None:
            raise ValueError("client is required for historical replay")
        rows = markets if markets is not None else self._resolved_yes_markets(category)
        records: list[dict] = []
        for market in rows:
            rec = self._score_market(market)
            if rec is not None:
                records.append(rec)
            if len(records) >= self.max_markets:
                break
        summary = alpha_test(records)
        return {
            "summary": summary,
            "records": records,
            "n_markets": len(records),
            "category": category,
            "predict_frac": self.predict_frac,
            "signal": getattr(self.signal_fn, "__name__", "signal"),
        }

    def _resolved_yes_markets(self, category: str | None) -> list:
        raw = self.client.list_resolved_markets(limit=self.max_markets * 5)
        yes = [m for m in self.client.to_markets(raw) if m.outcome == "YES"]
        if category:
            yes = [m for m in yes if categorize(m.question) == category]
        return yes

    def _score_market(self, market) -> dict | None:
        if not (market.price <= 0.05 or market.price >= 0.95):
            return None
        won = market.price >= 0.5
        candles = self.client.fetch_price_history(market.token_id, interval="max")
        n = len(candles)
        if n < self.min_history + 1:
            return None
        idx = min(max(int(self.predict_frac * n), self.min_history), n - 1)
        prediction_time = candles[idx].ts
        pit = [c for c in candles[:idx] if c.ts < prediction_time]
        if len(pit) < self.min_history:
            return None
        if any(c.ts >= prediction_time for c in pit):
            raise PointInTimeError(f"{market.token_id}: feature at/after prediction_time")
        market_p = pit[-1].close
        if not (0.02 < market_p < 0.98):
            return None
        p_model = float(self.signal_fn(pit, market_p))
        return {
            "status": "resolved",
            "won": bool(won),
            "p_true": p_model,
            "market_price": float(market_p),
            "question": market.question,
        }

    # ----- Lab MVP evidence path --------------------------------------------

    def run(self, request: BacktestRequest) -> BacktestRunResult:
        started = utc_now()
        run_id = f"bt_{_short_hash(request.hypothesis_id + request.strategy_id + started + uuid.uuid4().hex)}"
        prediction_time = request.time_window["end"]
        strategy = get_strategy(request.strategy_id)
        forecasts, market_sample, diagnostics = self._forecasts_with_report_rows(request, run_id)
        if not forecasts:
            forecasts = self._fixture_forecasts(request, run_id, prediction_time)
            market_sample = self._market_sample(forecasts, source="fixture")
            diagnostics = self._diagnostics(
                request,
                source="fixture",
                fetched=0,
                eligible=len(forecasts),
                skipped_by_category=0,
                skipped_unresolved=0,
                pit_warnings=[],
            )

        p_cal = [f.p_cal for f in forecasts]
        p_market = [f.p_market for f in forecasts]
        outcomes = [float(f.outcome) for f in forecasts if f.outcome is not None]
        summary = build_evaluation_summary(
            p_cal=p_cal,
            p_market=p_market,
            outcomes=outcomes,
            min_samples=30,
            pit_clean=not diagnostics["pit_warnings"],
        )
        report_id = f"eval_{_short_hash(run_id + request.hypothesis_id)}"
        report = {
            "id": report_id,
            "type": "evaluation_report",
            "hypothesis_id": request.hypothesis_id,
            "backtest_run_id": run_id,
            "scope": f"hypothesis:{request.hypothesis_id}",
            "time_window": dict(request.time_window),
            "backtest_config": {
                "market_filter": dict(request.market_filter),
                "max_markets": request.max_markets,
                "model_version": request.model_version,
                "prompt_version": request.prompt_version,
                "calibrator_id": request.calibrator_id,
                "pit_strict": request.pit_strict,
                "strategy_id": strategy.id,
                "signal_model_id": strategy.id,
            },
            "strategy": {
                "id": strategy.id,
                "description": strategy.description,
                "baseline": strategy.baseline,
                "available_strategies": sorted(STRATEGIES),
            },
            "market_universe": diagnostics["market_universe"],
            "data_quality": diagnostics["data_quality"],
            "metrics": asdict(summary),
            "scorecard": scorecard(p_cal=p_cal, p_market=p_market, outcomes=outcomes),
            "gates": promotion_gates(summary),
            "pit_warnings": diagnostics["pit_warnings"],
            "market_sample": market_sample,
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
        return self._forecasts_with_report_rows(request, run_id)[0]

    def _forecasts_with_report_rows(
        self,
        request: BacktestRequest,
        run_id: str,
    ) -> tuple[list[ForecastRecord], list[dict], dict]:
        if not hasattr(self.store, "fetch_collections"):
            return [], [], self._diagnostics(request, source="none", fetched=0)
        rows = self.store.fetch_collections(
            request.time_window["start"],
            request.time_window["end"],
            limit=request.max_markets,
        )
        category = request.market_filter.get("category")
        forecasts: list[ForecastRecord] = []
        market_sample: list[dict] = []
        pit_warnings: list[dict] = []
        skipped_by_category = 0
        skipped_unresolved = 0
        for row in rows:
            token = row["token_id"]
            question = row.get("question", "")
            if category and category != "all" and categorize(question) != category:
                skipped_by_category += 1
                continue
            raw = row.get("raw") or {}
            lab = raw.get("lab") or {}
            outcome = lab.get("outcome", raw.get("outcome"))
            if outcome is None:
                skipped_unresolved += 1
                continue
            prediction_time = row.get("as_of") or request.time_window["end"]
            available_at_max = lab.get("available_at_max") or raw.get("available_at_max") or prediction_time
            warning = self._pit_warning(token, available_at_max, prediction_time, strict=request.pit_strict)
            if warning:
                pit_warnings.append(warning)
                if request.pit_strict:
                    continue
            p_market = float(row.get("market_price") or lab.get("p_market") or 0.5)
            model_output = self._score_collection_model(raw, p_market, strategy_id=request.strategy_id)
            if "p_raw" in lab:
                model_output = {
                    **model_output,
                    "source": "lab_override",
                    "p_raw_model": model_output["p_raw"],
                    "p_raw": float(lab["p_raw"]),
                }
            p_raw = float(model_output["p_raw"])
            p_cal = float(lab.get("p_cal", self._calibrate_to_market(p_raw, p_market)))
            forecast = ForecastRecord(
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
            forecasts.append(forecast)
            market_sample.append(
                self._market_row(
                    forecast,
                    question=question,
                    source="collections",
                    signal_model=model_output,
                    snapshot_manifest=self._snapshot_manifest(
                        token=token,
                        row=row,
                        raw=raw,
                        prediction_time=prediction_time,
                        available_at_max=available_at_max,
                    ),
                )
            )
        return forecasts, market_sample, self._diagnostics(
            request,
            source="collections",
            fetched=len(rows),
            eligible=len(forecasts),
            skipped_by_category=skipped_by_category,
            skipped_unresolved=skipped_unresolved,
            pit_warnings=pit_warnings,
        )

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

    def _market_sample(self, forecasts: list[ForecastRecord], *, source: str) -> list[dict]:
        return [
            self._market_row(
                f,
                question=None,
                source=source,
                signal_model=self._fixture_signal_model(f.p_raw, f.p_market),
                snapshot_manifest={
                    "token_id": f.market_token_id,
                    "prediction_time": f.prediction_time,
                    "available_at_max": f.available_at_max,
                    "sources": [],
                    "pit_status": "clean",
                },
            )
            for f in forecasts
        ]

    @staticmethod
    def _market_row(
        forecast: ForecastRecord,
        *,
        question: str | None,
        source: str,
        signal_model: dict | None = None,
        snapshot_manifest: dict | None = None,
    ) -> dict:
        outcome = float(forecast.outcome) if forecast.outcome is not None else None
        brier_model = (forecast.p_cal - outcome) ** 2 if outcome is not None else None
        brier_market = (forecast.p_market - outcome) ** 2 if outcome is not None else None
        return {
            "market_token_id": forecast.market_token_id,
            "question": question,
            "source": source,
            "prediction_time": forecast.prediction_time,
            "available_at_max": forecast.available_at_max,
            "p_raw": forecast.p_raw,
            "p_cal": forecast.p_cal,
            "p_market": forecast.p_market,
            "outcome": forecast.outcome,
            "brier_model": brier_model,
            "brier_market": brier_market,
            "brier_delta": (brier_market - brier_model) if brier_model is not None else None,
            "absolute_error_model": abs(forecast.p_cal - outcome) if outcome is not None else None,
            "absolute_error_market": abs(forecast.p_market - outcome) if outcome is not None else None,
            "signal_model": signal_model,
            "snapshot_manifest": snapshot_manifest,
        }

    @staticmethod
    def _pit_warning(token: str, available_at: str | None, prediction_time: str, *, strict: bool) -> dict | None:
        try:
            assert_point_in_time(
                [{"feature": "collection", "available_at": available_at}],
                prediction_time,
                strict=strict,
            )
        except ValueError as exc:
            return {
                "market_token_id": token,
                "feature": "collection",
                "available_at": available_at,
                "prediction_time": prediction_time,
                "message": str(exc),
            }
        return None

    @staticmethod
    def _diagnostics(
        request: BacktestRequest,
        *,
        source: str,
        fetched: int,
        eligible: int | None = None,
        skipped_by_category: int = 0,
        skipped_unresolved: int = 0,
        pit_warnings: list[dict] | None = None,
    ) -> dict:
        warnings = pit_warnings or []
        requested = int(request.max_markets)
        eligible_markets = (
            eligible
            if eligible is not None
            else max(0, fetched - skipped_by_category - skipped_unresolved - len(warnings))
        )
        return {
            "market_universe": {
                "source": source,
                "requested_max_markets": requested,
                "fetched_markets": fetched,
                "eligible_markets": eligible_markets,
                "skipped_by_category": skipped_by_category,
                "skipped_unresolved": skipped_unresolved,
                "skipped_pit": len(warnings),
                "category": request.market_filter.get("category"),
                "settled_only": request.market_filter.get("settled_only"),
            },
            "data_quality": {
                "pit_clean": len(warnings) == 0,
                "pit_warning_count": len(warnings),
                "coverage_ratio": eligible_markets / requested if requested else 0.0,
                "uses_fixture_data": source == "fixture",
            },
            "pit_warnings": warnings,
        }

    @staticmethod
    def _calibrate_to_market(p_raw: float, p_market: float) -> float:
        return min(0.99, max(0.01, 0.7 * p_raw + 0.3 * p_market))

    @staticmethod
    def _score_collection(raw: dict, p_market: float, strategy_id: str | None = None) -> float:
        return float(BacktestRunner._score_collection_model(raw, p_market, strategy_id=strategy_id)["p_raw"])

    @staticmethod
    def _score_collection_model(raw: dict, p_market: float, strategy_id: str | None = None) -> dict:
        return get_strategy(strategy_id).predict(raw, p_market)

    @staticmethod
    def _fixture_signal_model(p_raw: float, p_market: float) -> dict:
        return {
            "id": "fixture-v1",
            "source": "deterministic_fixture",
            "baseline": "market_price",
            "p_market": p_market,
            "p_raw": p_raw,
            "score_delta": p_raw - p_market,
            "feature_vector": {},
            "feature_contributions": {},
            "weights": {},
        }

    @staticmethod
    def _snapshot_manifest(
        *,
        token: str,
        row: dict,
        raw: dict,
        prediction_time: str,
        available_at_max: str | None,
    ) -> dict:
        sources = []
        for name, value in raw.items():
            if name == "lab":
                continue
            if isinstance(value, dict):
                available_at = value.get("available_at") or value.get("as_of") or row.get("as_of")
                sources.append(
                    {
                        "source": name,
                        "available_at": available_at,
                        "fields": sorted(str(k) for k in value.keys())[:20],
                    }
                )
            else:
                sources.append(
                    {
                        "source": name,
                        "available_at": row.get("as_of"),
                        "fields": [],
                    }
                )
        return {
            "token_id": token,
            "snapshot_id": f"snap_{_short_hash(token + prediction_time)}",
            "prediction_time": prediction_time,
            "available_at_max": available_at_max,
            "sources": sources,
            "pit_status": "clean" if available_at_max and available_at_max <= prediction_time else "warning",
        }


def get_backtest_run(run_id: str) -> BacktestRunResult | None:
    return default_repository().get_backtest_run(run_id)


def get_report(report_id: str) -> dict | None:
    return default_repository().get_report(report_id)
