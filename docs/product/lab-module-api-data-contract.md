# AIHF Lab Module API And Data Contract

> Version: v0.1
> Date: 2026-06-25
> Status: contract-first draft
> Related: `lab-module-PRD.md`, `lab-module-hifi.md`, `lab-module-tests.md`

## 1. Scope

This document defines the first implementation contract for the Lab module. It is intentionally narrower than the full AIHF v0.2 architecture.

Lab MVP must support:

- create and list Hypotheses
- run a point-in-time backtest
- persist forecast and evaluation evidence
- render an EvaluationReport
- expose deterministic promote readiness

Lab MVP must not support:

- real order submission
- live portfolio mutation
- automatic Live promotion
- hidden LLM-only promotion decisions

## 2. Module Boundaries

```text
Ask
  produces research insight
  may request Lab Hypothesis creation

Lab
  owns Hypothesis creation
  owns backtest runs
  owns EvaluationReport generation
  owns paper-readiness gates

Library
  indexes reusable objects and reports
  does not run backtests

System
  owns tool manifest, permission policy, data health, audit
  Lab reads System state but does not mutate it in MVP

Live
  deferred
  cannot be called from Lab MVP
```

## 3. Naming Conventions

Python fields should use snake_case in new Lab modules.

Existing compatibility objects may still expose camelCase fields such as `snapshotId`. New Lab APIs should normalize to JSON snake_case.

IDs:

- Hypothesis: `hyp_<shortid>`
- BacktestRun: `bt_<shortid>`
- EvaluationReport: `eval_<shortid>`
- Forecast: `fc_<shortid>`
- Snapshot: `snap_<hash>`

Times:

- ISO 8601 UTC strings at API boundaries.
- `datetime` with timezone in Python internals.

## 4. Domain Schemas

### 4.1 Hypothesis

```json
{
  "id": "hyp_001",
  "type": "hypothesis",
  "version": 1,
  "state": "draft",
  "owner": "default",
  "statement": "Crypto news markets update slower than the model within 2 hours",
  "category_filter": "crypto",
  "feature_set": [],
  "prompt_version": "signal-v1",
  "model_version": "claude-sonnet-4",
  "snapshot_id": "snap_abc123",
  "lineage": {
    "source": "ask",
    "parents": [],
    "source_ref": "chat_123"
  },
  "eval_summary": null,
  "created_at": "2026-06-25T00:00:00Z",
  "updated_at": "2026-06-25T00:00:00Z"
}
```

`feature_set` is system-owned in the Lab UI. Users create the hypothesis and
optional market/category scope; ingestion and strategy backtests detect which
features are actually available from PIT-safe `DataStore.collections`.

Valid states for MVP:

- `draft`
- `lab`
- `archived`

Reserved states:

- `paper`
- `live`

### 4.2 BacktestRequest

```json
{
  "hypothesis_id": "hyp_001",
  "time_window": {
    "start": "2026-03-01T00:00:00Z",
    "end": "2026-06-01T00:00:00Z"
  },
  "market_filter": {
    "category": "crypto",
    "min_volume": 10000,
    "settled_only": true
  },
  "model_version": "claude-sonnet-4",
  "prompt_version": "signal-v1",
  "calibrator_id": "shrink-to-market-v1",
  "strategy_id": "linear-factor-v1",
  "pit_strict": true,
  "max_markets": 100
}
```

Validation:

- `start < end`
- `settled_only` must be true in MVP
- `strategy_id` defaults to `linear-factor-v1`
- allowed MVP strategies are `market-naive-v1`, `linear-factor-v1`,
  `momentum-v1`, `flow-imbalance-v1`, `microstructure-v1`, `sentiment-v1`,
  and `contrarian-v1`
- `pit_strict` defaults to true
- `max_markets` must be between 1 and 500

### 4.3 ForecastRecord

```json
{
  "id": "fc_001",
  "hypothesis_id": "hyp_001",
  "market_token_id": "token_yes",
  "snapshot_id": "snap_market_001",
  "p_raw": 0.68,
  "p_cal": 0.61,
  "p_market": 0.54,
  "outcome": 1,
  "model_version": "claude-sonnet-4",
  "prompt_version": "signal-v1",
  "calibrator_id": "shrink-to-market-v1",
  "prediction_time": "2026-04-10T12:00:00Z",
  "available_at_max": "2026-04-10T11:58:30Z"
}
```

PIT invariant:

```text
available_at_max <= prediction_time
```

Missing `available_at` values fail in strict mode.

### 4.4 EvaluationSummary

```json
{
  "n": 42,
  "brier_model": 0.14,
  "brier_market": 0.16,
  "brier_delta": 0.02,
  "brier_delta_ci": [0.008, 0.033],
  "ece": 0.03,
  "beats_market": true,
  "sample_adequate": false,
  "pit_clean": true
}
```

Rules:

- `brier_delta = brier_market - brier_model`
- positive `brier_delta` means the model beats market price
- `beats_market` is true only when `brier_delta_ci[0] > 0`
- `sample_adequate` is true only when `n >= min_samples`

### 4.5 EvaluationReport

```json
{
  "id": "eval_001",
  "type": "evaluation_report",
  "hypothesis_id": "hyp_001",
  "backtest_run_id": "bt_001",
  "scope": "hypothesis:hyp_001",
  "time_window": {
    "start": "2026-03-01T00:00:00Z",
    "end": "2026-06-01T00:00:00Z"
  },
  "backtest_config": {
    "market_filter": {
      "category": "crypto",
      "settled_only": true
    },
    "max_markets": 100,
    "model_version": "claude-sonnet-4",
    "prompt_version": "signal-v1",
    "calibrator_id": "shrink-to-market-v1",
    "pit_strict": true,
    "strategy_id": "linear-factor-v1",
    "signal_model_id": "linear-factor-v1"
  },
  "strategy": {
    "id": "linear-factor-v1",
    "description": "Deterministic linear factor model over stored collection snapshots.",
    "baseline": "market_price",
    "available_strategies": [
      "linear-factor-v1",
      "market-naive-v1",
      "momentum-v1",
      "flow-imbalance-v1",
      "microstructure-v1",
      "sentiment-v1",
      "contrarian-v1"
    ]
  },
  "market_universe": {
    "source": "collections",
    "requested_max_markets": 100,
    "fetched_markets": 42,
    "eligible_markets": 38,
    "skipped_by_category": 2,
    "skipped_unresolved": 2,
    "skipped_pit": 0,
    "category": "crypto",
    "settled_only": true
  },
  "data_quality": {
    "pit_clean": true,
    "pit_warning_count": 0,
    "coverage_ratio": 0.38,
    "uses_fixture_data": false
  },
  "metrics": {
    "n": 42,
    "brier_model": 0.14,
    "brier_market": 0.16,
    "brier_delta": 0.02,
    "brier_delta_ci": [0.008, 0.033],
    "ece": 0.03
  },
  "scorecard": {
    "model_log_loss": 0.41,
    "market_log_loss": 0.45,
    "calibration_bins": [],
    "market_calibration_bins": []
  },
  "gates": {
    "sample_adequate": false,
    "beats_market": true,
    "ece_pass": true,
    "pit_clean": true,
    "paper_ready": false
  },
  "pit_warnings": [],
  "market_sample": [
    {
      "market_token_id": "token_yes",
      "question": "Will BTC close above ...?",
      "p_cal": 0.61,
      "p_market": 0.54,
      "outcome": 1,
      "brier_model": 0.1521,
      "brier_market": 0.2116,
      "brier_delta": 0.0595,
      "signal_model": {
        "id": "linear-factor-v1",
        "source": "deterministic_factor_model",
        "baseline": "market_price",
        "p_market": 0.54,
        "p_raw": 0.68,
        "score_delta": 0.14,
        "feature_vector": {
          "sentiment": 0.4,
          "flow_imbalance": 0.3
        },
        "feature_contributions": {
          "sentiment": 0.072,
          "flow_imbalance": 0.036
        }
      },
      "snapshot_manifest": {
        "token_id": "token_yes",
        "snapshot_id": "snap_market_001",
        "prediction_time": "2026-04-10T12:00:00Z",
        "available_at_max": "2026-04-10T11:58:30Z",
        "pit_status": "clean",
        "sources": [
          {
            "source": "features",
            "available_at": "2026-04-10T11:58:30Z",
            "fields": ["factors"]
          }
        ]
      }
    }
  ],
  "generated_at": "2026-06-25T00:00:00Z"
}
```

`signal_model` is a per-sample explanation object. In MVP the default model is
`linear-factor-v1`, a deterministic weighted factor model. If historical
collection data already contains `lab.p_raw`, the report may mark the source as
`lab_override` while preserving the deterministic model output as `p_raw_model`.

`snapshot_manifest` is the point-in-time evidence manifest for the sample. It
lists the prediction time, latest known feature availability, and source fields
used to reconstruct the historical snapshot.

### 4.6 MonitorOpportunity

Dry-run only opportunity candidates from active markets:

```json
{
  "market_token_id": "token_yes",
  "question": "Will BTC close above 100k?",
  "strategy_id": "momentum-v1",
  "p_raw": 0.66,
  "p_cal": 0.61,
  "market_price": 0.52,
  "edge": 0.09,
  "apy": 1.2,
  "action": "buy",
  "size_usdc": 25.0,
  "dry_run": true,
  "reasons": [],
  "market": {
    "condition_id": "0x...",
    "outcome": "YES",
    "volume_24h": 100000.0,
    "liquidity": 25000.0,
    "days_to_expiry": 10.0
  },
  "signal_model": {
    "id": "momentum-v1",
    "baseline": "market_price",
    "feature_vector": {
      "price_momentum": 0.2,
      "flow_imbalance": 0.1
    },
    "feature_contributions": {
      "price_momentum": 0.056,
      "flow_imbalance": 0.006
    }
  }
}
```

Monitor output must always keep `dry_run=true` and must not call paper or live
execution endpoints.

## 5. Storage Contract

MVP adds these logical tables. Implementation may reuse `DataStore` or introduce a Lab-specific store, but table-level semantics must remain stable.

### 5.1 objects

```sql
CREATE TABLE objects (
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
```

### 5.2 forecasts

```sql
CREATE TABLE forecasts (
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
```

### 5.3 evaluations

```sql
CREATE TABLE evaluations (
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
```

### 5.4 promotion_events

```sql
CREATE TABLE promotion_events (
    id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    evidence_eval_id TEXT,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    FOREIGN KEY (object_id) REFERENCES objects(id)
);
```

### 5.5 audit_events

```sql
CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
```

## 6. API Contract

All endpoints are JSON unless noted.

### 6.1 `GET /api/lab/hypotheses`

Returns a compact list for the Lab navigator.

Query params:

- `state`
- `category`
- `limit`
- `cursor`

Response:

```json
{
  "items": [
    {
      "id": "hyp_001",
      "statement": "Crypto news markets update slower than the model",
      "state": "draft",
      "version": 1,
      "category_filter": "crypto",
      "latest_eval": {
        "n": 42,
        "brier_delta": 0.02,
        "beats_market": true,
        "sample_adequate": false
      },
      "updated_at": "2026-06-25T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

### 6.2 `POST /api/lab/hypotheses`

Creates a draft Hypothesis.

Request:

```json
{
  "statement": "Crypto news markets update slower than the model",
  "category_filter": "crypto",
  "feature_set": ["news_sentiment", "similar_markets"],
  "prompt_version": "signal-v1",
  "model_version": "claude-sonnet-4",
  "lineage": {
    "source": "manual",
    "parents": []
  }
}
```

Response:

```json
{
  "id": "hyp_001",
  "state": "draft",
  "version": 1,
  "snapshot_id": "snap_abc123"
}
```

### 6.3 `GET /api/lab/hypotheses/{id}`

Returns the full Hypothesis detail plus latest reports.

Response:

```json
{
  "hypothesis": {},
  "reports": [],
  "audit_tail": []
}
```

### 6.4 `POST /api/lab/hypotheses/{id}/backtests`

Runs or queues a Lab backtest.

Request is `BacktestRequest` without `hypothesis_id` in the body.

Response:

```json
{
  "backtest_run_id": "bt_001",
  "status": "completed",
  "report_id": "eval_001"
}
```

Allowed statuses:

- `queued`
- `running`
- `completed`
- `failed`

### 6.5 `GET /api/lab/backtests/{id}`

Returns run status and error detail.

Response:

```json
{
  "id": "bt_001",
  "hypothesis_id": "hyp_001",
  "status": "completed",
  "started_at": "2026-06-25T00:00:00Z",
  "finished_at": "2026-06-25T00:01:00Z",
  "error": null,
  "report_id": "eval_001"
}
```

### 6.6 `GET /api/lab/reports/{id}`

Returns an EvaluationReport.

Response is the EvaluationReport schema.

### 6.7 `POST /api/lab/monitor/opportunities`

Scans active markets with a selected Lab strategy. This endpoint is read-only
and dry-run only.

Request:

```json
{
  "strategy_id": "momentum-v1",
  "limit": 20,
  "min_volume_24h": 5000.0,
  "min_edge": 0.02,
  "include_holds": true
}
```

Response:

```json
{
  "strategy_id": "momentum-v1",
  "dry_run": true,
  "n": 1,
  "opportunities": [],
  "message": "ok",
  "errors": []
}
```

### 6.8 `GET /api/lab/system/status`

Read-only Lab view of System resources.

Response:

```json
{
  "tool_manifest_hash": "tm_abc123",
  "permission_policy": "lab-v1",
  "data_sources": [
    {
      "id": "polymarket",
      "status": "ok",
      "last_checked_at": "2026-06-25T00:00:00Z"
    }
  ],
  "live_tools_enabled": false
}
```

## 7. Permission Contract

Tool matrix:

| Tool | Ask | Lab | Live |
|---|---:|---:|---:|
| scan_markets | yes | yes | read-only |
| market_snapshot | yes | yes | read-only |
| evaluate_forecast | yes | yes | read-only |
| create_hypothesis | no | yes | no |
| run_backtest | no | yes | no |
| write_forecast | no | yes | no |
| write_evaluation | no | yes | no |
| paper_execute | no | optional-later | no |
| submit_order | no | no | yes |
| halt | no | no | yes |

Lab permission failures must:

- reject the tool call
- write an audit event
- return a structured error with `code = "permission_denied"`

## 8. Error Contract

Error response:

```json
{
  "error": {
    "code": "pit_violation",
    "message": "Feature available_at is after prediction_time",
    "details": {
      "market_token_id": "token_yes",
      "available_at": "2026-04-10T12:01:00Z",
      "prediction_time": "2026-04-10T12:00:00Z"
    }
  }
}
```

Codes:

- `validation_error`
- `not_found`
- `permission_denied`
- `insufficient_data`
- `pit_violation`
- `data_source_unavailable`
- `evaluation_failed`

## 9. Implementation Order

1. Add Lab schemas and pure evaluation summary helpers.
2. Add PIT assertion helper.
3. Add in-memory or SQLite Lab repository.
4. Add `BacktestRunner` contract.
5. Add API routes.
6. Add Lab UI using the API.

Tests should be written before each implementation step and should move from `xfail` to required passing tests as code lands.
