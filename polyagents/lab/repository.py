"""SQLite repository for Lab objects and evidence."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .schemas import BacktestRunResult, ForecastRecord, HypothesisRecord, utc_now


_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    version INTEGER NOT NULL,
    state TEXT NOT NULL,
    owner TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    lineage_json TEXT NOT NULL,
    eval_summary_json TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecasts (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL,
    market_token_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    p_raw REAL NOT NULL,
    p_cal REAL NOT NULL,
    p_market REAL NOT NULL,
    outcome INTEGER,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    calibrator_id TEXT NOT NULL,
    prediction_time TEXT NOT NULL,
    available_at_max TEXT,
    FOREIGN KEY (hypothesis_id) REFERENCES objects(id)
);
CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    backtest_run_id TEXT,
    metrics_json TEXT NOT NULL,
    gates_json TEXT NOT NULL,
    baseline_delta REAL NOT NULL,
    n_samples INTEGER NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL,
    status TEXT NOT NULL,
    report_id TEXT,
    forecast_count INTEGER NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS promotion_events (
    id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    evidence_eval_id TEXT,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    FOREIGN KEY (object_id) REFERENCES objects(id)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


class LabRepository:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def save_hypothesis(self, hypothesis: HypothesisRecord) -> None:
        payload = asdict(hypothesis)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO objects
            (id, type, version, state, owner, snapshot_id, lineage_json,
             eval_summary_json, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hypothesis.id,
                hypothesis.type,
                hypothesis.version,
                hypothesis.state,
                hypothesis.owner,
                hypothesis.snapshot_id,
                json.dumps(hypothesis.lineage, ensure_ascii=False),
                json.dumps(hypothesis.eval_summary, ensure_ascii=False) if hypothesis.eval_summary else None,
                json.dumps(payload, ensure_ascii=False),
                hypothesis.created_at,
                hypothesis.updated_at,
            ),
        )
        self.conn.commit()

    def list_hypotheses(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload_json FROM objects WHERE type='hypothesis' ORDER BY updated_at DESC"
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def get_hypothesis(self, hypothesis_id: str) -> HypothesisRecord | None:
        row = self.conn.execute(
            "SELECT payload_json FROM objects WHERE id=? AND type='hypothesis'",
            (hypothesis_id,),
        ).fetchone()
        if row is None:
            return None
        return HypothesisRecord(**json.loads(row["payload_json"]))

    def save_forecasts(self, forecasts: list[ForecastRecord]) -> None:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO forecasts
            (id, hypothesis_id, market_token_id, snapshot_id, p_raw, p_cal, p_market,
             outcome, model_version, prompt_version, calibrator_id, prediction_time, available_at_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f.id,
                    f.hypothesis_id,
                    f.market_token_id,
                    f.snapshot_id,
                    f.p_raw,
                    f.p_cal,
                    f.p_market,
                    f.outcome,
                    f.model_version,
                    f.prompt_version,
                    f.calibrator_id,
                    f.prediction_time,
                    f.available_at_max,
                )
                for f in forecasts
            ],
        )
        self.conn.commit()

    def forecasts_for_hypothesis(self, hypothesis_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM forecasts WHERE hypothesis_id=? ORDER BY prediction_time, id",
            (hypothesis_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_evaluation(self, report: dict[str, Any]) -> None:
        metrics = report["metrics"]
        gates = report["gates"]
        self.conn.execute(
            """
            INSERT OR REPLACE INTO evaluations
            (id, scope, hypothesis_id, backtest_run_id, metrics_json, gates_json,
             baseline_delta, n_samples, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["id"],
                report["scope"],
                report["hypothesis_id"],
                report.get("backtest_run_id"),
                json.dumps(metrics, ensure_ascii=False),
                json.dumps(gates, ensure_ascii=False),
                float(metrics.get("brier_delta", 0.0)),
                int(metrics.get("n", 0)),
                report.get("generated_at") or utc_now(),
            ),
        )
        self.conn.commit()

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM evaluations WHERE id=?", (report_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "type": "evaluation_report",
            "hypothesis_id": row["hypothesis_id"],
            "backtest_run_id": row["backtest_run_id"],
            "scope": row["scope"],
            "metrics": json.loads(row["metrics_json"]),
            "gates": json.loads(row["gates_json"]),
            "generated_at": row["generated_at"],
        }

    def reports_for_hypothesis(self, hypothesis_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id FROM evaluations WHERE hypothesis_id=? ORDER BY generated_at DESC",
            (hypothesis_id,),
        ).fetchall()
        return [report for r in rows if (report := self.get_report(r["id"])) is not None]

    def save_backtest_run(self, result: BacktestRunResult) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO backtest_runs
            (id, hypothesis_id, status, report_id, forecast_count, error, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.id,
                result.hypothesis_id,
                result.status,
                result.report_id,
                result.forecast_count,
                result.error,
                result.started_at,
                result.finished_at,
            ),
        )
        self.conn.commit()

    def get_backtest_run(self, run_id: str) -> BacktestRunResult | None:
        row = self.conn.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return BacktestRunResult(
            id=row["id"],
            hypothesis_id=row["hypothesis_id"],
            status=row["status"],
            report_id=row["report_id"],
            forecast_count=row["forecast_count"],
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def counts(self) -> dict[str, int]:
        tables = ("objects", "forecasts", "evaluations", "backtest_runs", "promotion_events", "audit_events")
        return {
            table: self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            for table in tables
        }
