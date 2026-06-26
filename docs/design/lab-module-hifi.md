# AIHF Lab Module High-Fidelity Design

> Version: v0.1
> Date: 2026-06-25
> Reference model: OpenAlice-style dense workspace, adapted to AIHF v0.2

## 1. Design Intent

Lab should feel like a research cockpit, not a landing page. The first screen should immediately expose research objects, backtest status, and evaluation evidence.

The design borrows OpenAlice's structure:

- persistent left global navigation
- second-column workspace/object navigation
- central task workspace
- contextual right-side evidence panel when needed

It does not copy OpenAlice labels or trading concepts directly. AIHF's core objects remain Market, Hypothesis, Strategy, Position, and Portfolio.

## 2. Layout

```text
┌────────────────┬─────────────────────┬──────────────────────────────────────┬──────────────────────┐
│ Global Nav     │ Lab Navigator        │ Main Workspace                       │ Evidence Panel       │
│                │                     │                                      │                      │
│ Ask            │ Overview             │ Hypothesis detail / backtest runner  │ Metrics              │
│ Lab selected   │ Hypotheses           │ Evaluation report                    │ PIT checks           │
│ Live disabled  │ Backtests            │ Empty states                         │ Lineage              │
│ Library        │ Reports              │                                      │ Audit                │
│ System         │ Promote Queue        │                                      │                      │
└────────────────┴─────────────────────┴──────────────────────────────────────┴──────────────────────┘
```

Responsive behavior:

- Desktop: four-column cockpit.
- Tablet: global nav collapses to icons; evidence panel becomes a drawer.
- Mobile: Lab Navigator becomes a top segmented control; evidence panel opens as full-screen sheet.

## 3. Global Navigation

Items:

- Ask
- Lab
- Live
- Library
- System

States:

- Ask: available, owned by teammate.
- Lab: active for this module.
- Live: visible but disabled or marked "Later" until Lab evidence gates exist.
- Library: shared object catalog.
- System: settings and audit.

Visual treatment:

- Dark utilitarian shell.
- Compact labels.
- Icons plus text on desktop.
- No marketing hero.
- No decorative gradients.

## 4. Lab Navigator

Sections:

- Overview
- Hypotheses
- Backtests
- Reports
- Promote Queue

Each Hypothesis list item shows:

- short statement
- state badge: `draft`, `lab`, `paper-ready`, `archived`
- latest metric: `Brier delta`, `n`
- last updated time

Filters:

- state
- category
- owner
- result: positive, inconclusive, failed

## 5. Lab Overview Screen

Purpose:

- show research pipeline health
- surface what needs attention

Blocks:

- Active Hypotheses
- Backtests running
- Reports needing review
- Promote candidates
- Data health warnings

Primary command:

- Create Hypothesis

Secondary commands:

- Import from Ask
- Browse Library
- Review failed PIT checks

## 6. Hypothesis Detail Screen

Header:

- Hypothesis id and version
- state
- short statement
- lineage source
- latest evaluation status

Main content:

- Statement
- Category filter
- Feature set
- Prompt/model versions
- Snapshot id
- Evaluation summary

Commands:

- Run backtest
- Duplicate version
- Archive
- View in Library

Disabled until later:

- Promote to Live

## 7. Backtest Runner Panel

Inputs:

- time window
- category filter
- market count limit
- model version
- prompt version
- calibrator
- PIT strictness

Before-run validation:

- enough settled markets
- required data source available
- PIT mode enabled
- tool manifest hash visible

Run states:

- idle
- validating
- queued
- running
- completed
- failed

Failure states must be specific:

- insufficient data
- PIT violation
- missing model key
- data source unavailable
- evaluation write failed

## 8. EvaluationReport Screen

Top summary:

- verdict: positive, inconclusive, failed
- sample size
- Brier model
- Brier market
- Brier delta
- 95% CI
- ECE

Evidence sections:

- metric table
- market sample table
- calibration view
- PIT check summary
- assumptions and caveats
- promotion gate status

Promotion gate display:

```text
Sample size        pass/fail
CI lower bound     pass/fail
ECE threshold      pass/fail
PIT clean          pass/fail
```

The UI should never imply that a Hypothesis is tradeable just because a single metric is green.

## 9. Library Integration

Lab writes objects into Library:

- Hypotheses
- EvaluationReports
- Strategy candidates

Library is the shared catalog. Lab is the workspace for creating and evaluating those objects.

Lab object links should open Library detail pages later, but MVP can keep them as internal anchors.

## 10. System Integration

System owns:

- Lab tool manifest
- permission policy
- audit events
- data source health
- model configuration

Lab reads System status but does not modify System configuration in MVP.

## 11. Empty States

No Hypotheses:

- Show a compact creation form.
- Offer import from Ask.
- Show no hero illustration.

No Reports:

- Explain that reports appear after backtests.
- Primary command: Run backtest on selected Hypothesis.

Insufficient Data:

- Show exact missing requirement.
- Suggest widening time window or selecting another category.

## 12. Visual System

Tone:

- dense
- operational
- calm
- evidence-first

Avoid:

- oversized cards
- marketing copy
- decorative blobs
- single-hue palette
- hiding metrics behind chat

Use:

- compact tables
- badges for state
- segmented controls for Lab sections
- icon buttons for actions
- clear disabled states
- fixed-width metric panels

## 13. MVP Screen List

Required:

- Lab Overview
- Hypothesis List
- Hypothesis Detail
- Backtest Runner
- EvaluationReport Detail

Deferred:

- Strategy Builder
- Notebook view
- Live approval drawer
- Multi-user comments
