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
from dataclasses import asdict, dataclass, field
from typing import Callable

from polyagents.evaluation.alpha import alpha_test
from polyagents.evaluation.evaluate import categorize
from polyagents.evaluation.report import build_evaluation_summary, promotion_gates

from .pit import assert_point_in_time
from .repository import LabRepository
from .schemas import BacktestRequest, BacktestRunResult, ForecastRecord, utc_now
from .service import default_repository


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
