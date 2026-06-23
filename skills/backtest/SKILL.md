---
name: backtest
description: Backtest and diagnose prediction-market strategy logic using MerakkuFund's evaluation and qlib backtest surfaces. Use after a strategy idea, signal rule, or run result needs validation.
source: HKUDS/Vibe-Trading agent/src/skills/backtest-diagnose
---

# Backtest

Use this skill when a Polymarket strategy needs validation, or when a previous
test produced a failure, no trades, or suspicious metrics. It adapts
Vibe-Trading's backtest-diagnosis checklist to MerakkuFund's current framework.

## Workflow

1. Start from data: use `market_snapshot` and, when available,
   `find_similar_markets` to define the market universe and evidence.
2. Name the research claim as a `Hypothesis` with `id`, `version`, `state`,
   `snapshotId`, and `lineage`.
3. Validate the signal rule as a `Strategy`: make sure the rule can emit buy, sell, and hold,
   and that it does not look ahead to settlement data.
4. Run the available backtest/evaluation surface:
   - `evaluation_report()` for forecast-quality diagnostics against market price
   - `qlib-backtest` MCP tools when testing historical factor strategies
5. Produce an `EvaluationReport` JSON with deterministic risk metrics and
   `promotionRecommendation`.
6. Read generated artifacts before changing logic. Prefer fixing one issue at a
   time and rerunning the test.
7. Report metrics with caveats: sample size, hit rate, Brier/log loss,
   drawdown, P&L, and whether the model beats the market baseline.

## EvaluationReport Contract

An adapted MarketLens backtest must not stop at Markdown. It should also produce
an `EvaluationReport` JSON containing:

- `hypothesisId`, `strategyId`, `inputQuery`, `parameters`
- `marketCount`, `tradeCount`, `totalPnl`, `winRate`
- `maxDrawdown`, `sharpe`, `profitFactor`, `riskRating`
- `caveats`, `promotionRecommendation`

Promotion recommendations are deterministic:

- insufficient sample size or high risk -> `remain_draft`
- promising but not paper-ready -> `promote_to_lab`
- sufficient sample, positive P&L, acceptable drawdown, Sharpe, profit factor,
  and non-high risk -> `promote_to_paper`

Do not automatically promote; write the recommendation as evidence only.

## Failure taxonomy

- No trades: rule too strict, missing token universe, or all risk gates fail.
- Late trades: history window or expiry filter is too narrow.
- Overstated P&L: fill assumptions ignore spread, depth, or slippage.
- Good return but poor calibration: signal may be lucky rather than predictive.
- Provider/data failure: do not rewrite strategy logic until the data issue is
  isolated.

## Hard gates

- No lookahead to resolved outcome.
- Include execution costs or paper fills when reporting tradability.
- Do not call a strategy deployable unless it beats a market-price baseline and
  survives liquidity/spread checks.
- pi.dev may assist as a coding harness in Lab via MCP, but it is not the
  backtest engine and must not make promotion decisions.
