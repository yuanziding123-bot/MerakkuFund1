# AIHF Lab Module PRD

> Version: v0.1
> Date: 2026-06-25
> Owner: Lab
> Base branch: `adapt/vibe-trading-skills`
> Reference: `AIHF 产品 PRD 与技术架构 v0.2`

## 1. Product Position

Lab is the AIHF research workspace. Its job is to turn a market idea into a versioned, testable, evidence-backed financial object.

Lab is not a separate engine. It is the same AIHF agent runtime running in `lab` mode with a broader tool manifest than Ask and a safer permission policy than Live.

The core Lab loop is:

```text
Hypothesis draft -> Backtest run -> EvaluationReport -> promote readiness
```

For MVP, Lab stops before Live execution. It can create and evaluate research artifacts, but it cannot submit real orders.

## 2. Users And Jobs

Primary user:

- AIHF researcher/operator who wants to validate whether an edge exists before it becomes a paper or live strategy.

Core jobs:

- Convert an Ask insight into a named Hypothesis.
- Inspect all active and historical Hypotheses.
- Run point-in-time backtests over selected markets and windows.
- Compare model probabilities against market prices.
- Read an EvaluationReport with sample size, Brier delta, confidence interval, ECE, and caveats.
- Decide whether a Hypothesis is ready to become a paper Strategy.

## 3. MVP Scope

In scope:

- Lab landing workspace.
- Hypothesis list and detail view.
- Create Hypothesis form.
- Backtest configuration panel.
- Backtest run status.
- EvaluationReport detail view.
- Evidence-based promote readiness indicator.
- Read-only links to related Market objects and Library resources.
- Audit trail for Lab actions.

Out of scope:

- Real-money trading.
- Multi-user permissions.
- Complex portfolio simulation.
- Fully automated strategy mining.
- Custom notebook environment.
- Live execution approvals.

## 4. Product Model

Lab operates on the v0.2 financial object chain:

```text
Market -> Hypothesis -> Strategy -> Position -> Portfolio
```

MVP Lab owns:

- `Hypothesis`
- `EvaluationReport`
- Draft `Strategy` readiness signal, without full Strategy creation unless explicitly enabled later.

Required Hypothesis fields:

- `id`
- `version`
- `state`
- `statement`
- `category_filter`
- `feature_set`
- `prompt_version`
- `model_version`
- `snapshot_id`
- `lineage`
- `eval_summary`
- `created_at`

Required EvaluationReport fields:

- `id`
- `scope`
- `hypothesis_id`
- `time_window`
- `n_samples`
- `brier_model`
- `brier_market`
- `brier_delta`
- `brier_delta_ci`
- `ece`
- `beats_market`
- `sample_adequate`
- `pit_warnings`
- `generated_at`

Metric convention:

- `brier_delta = brier_market - brier_model`
- positive `brier_delta` means the model beats the market baseline
- `beats_market` is true only when `brier_delta_ci[0] > 0`

## 5. User Stories

### L1: Create Hypothesis

As a researcher, I can create a Hypothesis from a natural-language idea so that it becomes a versioned object in Lab.

Acceptance:

- The Hypothesis receives a stable id.
- Initial state is `draft`.
- It has a `snapshot_id`.
- Its lineage records whether it came from Ask, manual creation, or another Hypothesis.

### L2: Configure Backtest

As a researcher, I can select a category, market set, and time window so that the backtest scope is explicit.

Acceptance:

- Time window is required.
- Market category filter is visible.
- Feature set and model version are visible.
- The UI shows whether there is enough historical data before running.

### L3: Run Backtest

As a researcher, I can run a point-in-time backtest so that I can evaluate the Hypothesis against market price.

Acceptance:

- The backtest records `prediction_time` and `snapshot_id`.
- Every signal input passes `available_at <= prediction_time`.
- The run writes forecasts and an evaluation record.
- Failures show actionable errors without losing the Hypothesis.

### L4: Read EvaluationReport

As a researcher, I can inspect metrics and caveats so that I can judge whether the edge is real.

Acceptance:

- Report includes model Brier, market Brier, delta, bootstrap 95% CI, ECE, and sample size.
- `beats_market` is true only when the CI lower bound is above 0.
- `sample_adequate` is visible and separate from performance.
- Report links back to the Hypothesis and sampled markets.

### L5: Promote Readiness

As a researcher, I can see whether a Hypothesis is ready to become a paper Strategy.

Acceptance:

- Readiness is deterministic, not LLM judged.
- MVP readiness checks include sample adequacy, positive CI lower bound, and ECE threshold.
- If not ready, the UI states which gate failed.
- No real execution action is available in Lab.

## 6. Navigation And IA

Top-level modules:

- Ask
- Lab
- Live
- Library
- System

Lab second-level areas:

- Overview
- Hypotheses
- Backtests
- Reports
- Promote Queue

Library owns shared resources:

- Markets
- Datasets
- Hypotheses
- Strategies
- EvaluationReports
- Templates

System owns operational resources:

- Tool manifests
- Permission policies
- MCP servers
- Audit events
- Data source health
- Model settings

## 7. Permissions

Lab mode can:

- Read markets.
- Read snapshots.
- Create Hypotheses.
- Run backtests.
- Write forecasts and evaluations.
- Run sandboxed code execution later.

Lab mode cannot:

- Submit orders.
- Modify live portfolios.
- Change live risk limits.
- Bypass PIT checks.
- Promote to Live without explicit future policy.

## 8. Success Metrics

MVP is successful when:

- A user can create a Hypothesis and run a backtest without editing code.
- The EvaluationReport clearly compares model performance against market price.
- PIT invariant tests pass.
- Lab artifacts are reusable from Library.
- Ask and Lab can share objects without sharing UI ownership.

## 9. Implementation Notes

Suggested modules:

- `polyagents/lab/backtest.py`
- `polyagents/evaluation/ledger.py`
- `polyagents/evaluation/report.py`
- `polyagents/runtime/session.py`
- `polyagents/web/` Lab routes and static UI

Do not create a new top-level app folder. Use the existing `polyagents` package and add only the missing Lab submodules.
