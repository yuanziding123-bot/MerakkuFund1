# AIHF Lab Progress Report

> Date: 2026-06-25
> Branch: `codex/lab-module-prd-design-tests`
> Scope: Lab module planning, contracts, core implementation, persistence, and backtest data path

## Brief Summary

Lab has moved from planning into a working MVP foundation. We now have product specs, high-fidelity design notes, API/data contracts, contract tests, Lab schemas, PIT checks, probability evaluation gates, permissions, SQLite persistence, Lab API routes, and a BacktestRunner that can read stored `DataStore.collections` before falling back to deterministic fixtures. The current verified path is:

```text
Hypothesis -> DataStore collections -> Forecasts -> EvaluationReport -> SQLite persistence
```

Relevant tests are passing:

```text
32 passed in 0.52s
```

No Live execution path has been enabled.

## 1. Branch And Repo State

Current working branch:

```text
codex/lab-module-prd-design-tests
```

This branch was created from `adapt/vibe-trading-skills`.

The previous local `adapt` branch from the unrelated old repo history was preserved as:

```text
legacy/local-adapt-backtest-skill
```

Remote context:

- `amber`: `https://github.com/AmberEigent/MerakkuFund.git`
- target upstream conceptually remains `amber/adapt-vibe-trading-skills`
- `amber/main` has newer Ask/product changes, but direct merge into adapt produced multiple conflicts, so it has not been merged yet

## 2. Product And Design Work Completed

Created Lab planning docs:

- `docs/product/lab-module-PRD.md`
- `docs/design/lab-module-hifi.md`
- `docs/product/lab-module-tests.md`
- `docs/product/lab-module-api-data-contract.md`

These documents define:

- Lab module scope and non-goals
- Lab information architecture
- OpenAlice-inspired workspace layout
- Hypothesis -> Backtest -> EvaluationReport flow
- API contract
- SQLite data contract
- permission matrix
- error contract
- test plan

Product boundary now stands as:

- Ask owns chat and idea discovery
- Lab owns Hypothesis creation, backtesting, reports, and paper-readiness gates
- Library owns shared object catalogs
- System owns tool manifests, policies, audit, data health
- Live remains deferred

## 3. Lab Core Implemented

Added new Lab package:

- `polyagents/lab/__init__.py`
- `polyagents/lab/schemas.py`
- `polyagents/lab/pit.py`
- `polyagents/lab/service.py`
- `polyagents/lab/backtest.py`
- `polyagents/lab/repository.py`

Core capabilities now implemented:

- `CreateHypothesisRequest`
- `CreateHypothesisResponse`
- `HypothesisRecord`
- `BacktestRequest`
- `ForecastRecord`
- `BacktestRunResult`
- PIT invariant checks via `assert_point_in_time`
- Hypothesis creation
- Hypothesis list/detail retrieval
- Backtest run result persistence
- Forecast persistence
- Evaluation persistence

## 4. Evaluation And Gates Implemented

Added:

- `polyagents/evaluation/report.py`

Implemented:

- `EvalSummary`
- `build_evaluation_summary`
- `promotion_gates`

Evaluation behavior:

- `brier_delta = brier_market - brier_model`
- positive delta means model beats market
- `beats_market` requires CI lower bound above 0
- sample adequacy is tracked separately from performance
- paper readiness requires sample adequacy, beats-market gate, ECE gate, and PIT clean gate

This aligns with the v0.2 principle that market price is the baseline and promotion must be deterministic, not LLM-judged.

## 5. Runtime Permissions Implemented

Added:

- `polyagents/runtime/__init__.py`
- `polyagents/runtime/session.py`

Implemented:

- `PermissionPolicy.for_mode("ask" | "lab" | "live")`
- `AgentSession`
- `PermissionDenied`
- simple audit sink

Important behavior:

- Lab can create Hypotheses and run backtests
- Lab cannot submit orders
- Live-only tools remain blocked outside Live mode

## 6. Persistence Implemented

Added SQLite repository:

- `polyagents/lab/repository.py`

Tables created:

- `objects`
- `forecasts`
- `evaluations`
- `backtest_runs`
- `promotion_events`
- `audit_events`

Default Lab DB path:

```text
.polyagents/cache/lab.db
```

Override:

```text
POLYAGENTS_LAB_DB
```

Tests use tmp SQLite databases, so test runs do not persist data into the workspace.

## 7. API Routes Implemented

Updated:

- `polyagents/web/server.py`

Added endpoints:

- `GET /api/lab/hypotheses`
- `POST /api/lab/hypotheses`
- `GET /api/lab/hypotheses/{id}`
- `POST /api/lab/hypotheses/{id}/backtests`
- `GET /api/lab/backtests/{id}`
- `GET /api/lab/reports/{id}`
- `GET /api/lab/system/status`

These endpoints are enough for the first Lab UI flow:

```text
Create Hypothesis -> Run Backtest -> Open Report
```

## 8. Backtest Data Path Implemented

Updated:

- `polyagents/storage/db.py`
- `polyagents/lab/backtest.py`

`DataStore` now exposes:

- `fetch_collections(min_as_of, max_as_of, limit)`

`BacktestRunner` now:

- prefers stored `DataStore.collections`
- filters by time window and category
- enforces PIT checks
- derives p_raw/p_cal/p_market/outcome records
- writes forecasts and EvaluationReport to LabRepository
- falls back to deterministic fixture data only when no usable collections exist

This is the first step toward real historical replay.

## 9. Tests Added

Added:

- `tests/test_lab_api_contract.py`
- `tests/test_lab_backtest_contract.py`
- `tests/test_lab_evaluation_contract.py`
- `tests/test_lab_permissions_contract.py`
- `tests/test_lab_repository.py`

These cover:

- Lab route registration
- Hypothesis creation response shape
- Backtest request validation
- PIT violation rejection
- Backtest persistence
- stored collection replay
- Brier/ECE evaluation behavior
- promotion gates
- Lab permission boundaries

## 10. Verification

Most recent relevant test run:

```text
./MerakkuFund/.venv/bin/python -m pytest \
  tests/test_lab_repository.py \
  tests/test_lab_api_contract.py \
  tests/test_lab_backtest_contract.py \
  tests/test_lab_evaluation_contract.py \
  tests/test_lab_permissions_contract.py \
  tests/test_web.py \
  tests/test_evaluation.py \
  tests/test_aihf_v02_objects.py \
  tests/test_storage.py
```

Result:

```text
32 passed in 0.52s
```

Note: local shell does not expose `pytest` directly, and system `python3` does not have pytest installed, so tests were run with:

```text
./MerakkuFund/.venv/bin/python
```

## 11. Not Touched

These large untracked directories remain untouched and should not be included in this Lab commit:

- `Alpha-devbox/`
- `MerakkuFund/`
- `node-v22.21.1-win-x64/`

## 12. Remaining Work

Recommended next implementation steps:

1. Build Lab UI pages on top of the new `/api/lab/*` routes.
2. Replace heuristic `_score_collection` with a proper signal/calibration path.
3. Add report detail fields back into persisted `evaluations`, including `time_window`, `market_sample`, and PIT warnings.
4. Add audit event writes for API calls and permission failures.
5. Decide how to reconcile `amber/main` Ask changes with `adapt/vibe-trading-skills` without mixing the conflict resolution into the Lab MVP commit.

## 13. Git Recommendation

Do not push the entire working tree as-is.

Recommended commit scope:

- `docs/product/lab-module-PRD.md`
- `docs/product/lab-module-api-data-contract.md`
- `docs/product/lab-module-tests.md`
- `docs/product/lab-progress-report-2026-06-25.md`
- `docs/design/lab-module-hifi.md`
- `polyagents/lab/*`
- `polyagents/evaluation/report.py`
- `polyagents/runtime/*`
- `polyagents/storage/db.py`
- `polyagents/web/server.py`
- `tests/test_lab_*.py`

Exclude:

- `Alpha-devbox/`
- `MerakkuFund/`
- `node-v22.21.1-win-x64/`

Recommended commit message:

```text
feat(lab): add MVP contracts, persistence, and backtest evidence path
```

Recommended push target:

```text
codex/lab-module-prd-design-tests
```

After pushing, open a draft PR into:

```text
adapt/vibe-trading-skills
```

Do not merge `amber/main` into this branch before review. That should be a separate conflict-resolution branch.
