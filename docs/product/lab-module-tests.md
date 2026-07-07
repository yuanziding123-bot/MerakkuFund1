# AIHF Lab Module Test Plan

> Version: v0.1
> Date: 2026-06-25
> Scope: Lab MVP before implementation

## 1. Test Strategy

Lab tests must prove that research artifacts are created, evaluated, and gated deterministically before any Live behavior exists.

Test layers:

- product acceptance tests
- unit tests for objects and state transitions
- integration tests for backtest and evaluation ledger
- permission tests for Lab mode
- UI/API contract tests
- PIT invariant tests

## 2. Product Acceptance Cases

### TC-LAB-001: Create Hypothesis

Given the user is in Lab
When they create a Hypothesis with statement and category filter
Then the system creates a versioned Hypothesis in `draft` state
And assigns `snapshot_id`
And writes lineage
And leaves feature selection to the ingestion / backtest pipeline
And shows it in the Hypothesis list.

### TC-LAB-002: Import Hypothesis From Ask

Given Ask produced a research suggestion
When the user chooses to validate it in Lab
Then Lab creates a Hypothesis whose lineage source is Ask
And the original Ask context is referenced
And no backtest runs automatically.

### TC-LAB-003: Configure Backtest

Given a Hypothesis exists
When the user opens the Backtest Runner
Then time window, market filter, model version, prompt version, and calibrator are visible
And the Run command is disabled until required fields are valid.

### TC-LAB-004: Run Backtest

Given a valid Hypothesis and enough settled markets
When the user runs a backtest
Then forecasts are written with `p_raw`, `p_cal`, `p_market`, `prediction_time`, and `snapshot_id`
And an evaluation record is written
And the Hypothesis latest `eval_summary` is updated.

### TC-LAB-004A: Run Strategy-Aware Backtest

Given the same settled-market collections
When the user runs the Lab strategy registry
Then each run writes an EvaluationReport with its `strategy_id`
And `market-naive-v1` acts as the market-price baseline
And non-baseline strategies expose feature vectors and feature contributions.

### TC-LAB-004B: Dry-Run Monitor

Given active markets and a selected strategy
When the user scans monitor opportunities
Then the response contains candidate opportunities or an explicit no-opportunity result
And every opportunity has `dry_run=true`
And no paper or live execution call is triggered.

### TC-LAB-005: Show EvaluationReport

Given a completed backtest
When the user opens the report
Then the report shows Brier model, Brier market, Brier delta, bootstrap CI, ECE, sample size, and verdict
And links back to the Hypothesis.

### TC-LAB-006: Gate Is Evidence-Based

Given an EvaluationReport
When the CI lower bound is not above 0
Then `beats_market` is false
And the promote readiness state is not ready even if point estimate is positive.

### TC-LAB-007: Insufficient Sample

Given an EvaluationReport with `n_samples` below threshold
When the user views promote readiness
Then the sample gate fails
And the UI shows sample inadequacy separately from model performance.

### TC-LAB-008: PIT Violation

Given a feature row whose `available_at` is after `prediction_time`
When a backtest tries to use it
Then the backtest fails
And no success EvaluationReport is produced
And the error records a PIT violation.

### TC-LAB-009: Lab Cannot Submit Orders

Given the session mode is Lab
When the user or agent attempts to call a Live-only order submission tool
Then permission policy rejects the call
And an audit event is written.

### TC-LAB-010: Archive Hypothesis

Given a Hypothesis exists
When the user archives it
Then the state becomes `archived`
And previous EvaluationReports remain readable.

## 3. Unit Test Targets

Suggested files:

- `tests/test_lab_objects.py`
- `tests/test_lab_backtest.py`
- `tests/test_lab_permissions.py`
- `tests/test_lab_evaluation_report.py`

Object tests:

- Hypothesis creates with valid defaults.
- Version increments by creating a new object, not mutating old one.
- Invalid state transition is rejected.
- Lineage is preserved.

Evaluation tests:

- Brier score is computed correctly.
- Brier delta is model-vs-market.
- `beats_market` requires CI lower bound above 0.
- `sample_adequate` follows configured threshold.
- ECE threshold is applied independently.

PIT tests:

- `available_at <= prediction_time` passes.
- `available_at > prediction_time` fails.
- Missing `available_at` fails in strict mode.

Permission tests:

- Ask cannot create Hypothesis.
- Lab can create Hypothesis and run backtest.
- Lab cannot submit order.
- Live can access read-only evaluation report.

## 4. Integration Test Targets

Backtest integration:

- Build a fake settled market set.
- Build fake historical snapshots.
- Run `BacktestRunner`.
- Assert forecasts and evaluation are written.
- Assert report is linked to Hypothesis.

Ledger integration:

- Insert object.
- Insert forecasts.
- Generate evaluation.
- Re-read by Hypothesis id.
- Confirm JSON payload round-trip.

Web/API contract:

- `GET /lab/hypotheses` returns list fields needed by navigator.
- `POST /lab/hypotheses` creates a draft object.
- `POST /lab/hypotheses/{id}/backtests` starts or runs a backtest.
- `GET /lab/reports/{id}` returns report metrics and gate status.

## 5. Manual QA Checklist

- Lab appears as a top-level module.
- Live is visible but not executable.
- Library links are visible from Lab objects.
- System status is read-only from Lab.
- Empty states are useful and compact.
- Backtest failure messages are specific.
- EvaluationReport does not overstate tradeability.
- No real order path is exposed.

## 6. Definition Of Done

Before merging Lab MVP:

- Product PRD is updated.
- High-fidelity design is updated.
- Test plan is updated.
- Unit tests pass locally.
- Integration tests pass locally.
- No Live execution path is enabled.
- Any merge from `amber/main` has a separate conflict-resolution PR or commit.
