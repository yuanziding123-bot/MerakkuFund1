---
name: risk-analysis
description: Risk measurement for prediction-market trades and portfolios. Use for VaR-style loss framing, drawdown, exposure, Kelly risk, stress scenarios, and post-trade risk review.
source: HKUDS/Vibe-Trading agent/src/skills/risk-analysis
---

# Risk analysis

Use this skill to evaluate whether a Polymarket trade or portfolio is worth the
loss profile. It adapts Vibe-Trading's VaR, CVaR, drawdown, and stress-testing
methodology to binary payout markets.

## Workflow

1. Identify the object under review: `Hypothesis`, `Strategy`, `Position`, or
   `Portfolio`.
2. Use `portfolio_status()` to inspect cash, exposure, open positions, and
   realized P&L.
3. Use `size_position(p_true, token_id)` to inspect calibrated edge, Kelly
   fraction, annualized edge, and risk-gate reasons.
4. Use `pnl_report()` after settlements to review hit rate, realized P&L, and
   decision mix.
5. Use `evaluation_report()` to check whether estimated probabilities are
   calibrated and whether they beat market prices.
6. Feed the risk result into an `EvaluationReport` and deterministic
   `promotionRecommendation`.

## AIHF v0.2 Promotion Gates

Risk evaluation is evidence for gates; it does not mutate state by itself.

- `Hypothesis` remains `draft` when sample size is small or risk is high.
- `Strategy` can be recommended for `paper` only when risk is not high and the
  backtest has enough trades, positive P&L, acceptable drawdown, Sharpe, and
  profit factor.
- `Position` and `Portfolio` checks can block additional exposure, but cannot
  create new hypotheses or strategies.

The agent should state one recommendation: `remain_draft`, `promote_to_lab`, or
`promote_to_paper`.

## Risk checks

- Position loss at default: for a long YES/NO token, max loss is premium paid.
- Liquidity risk: thin books and wide spreads can make exit prices much worse
  than marked prices.
- Correlation risk: several markets can depend on the same event, candidate,
  court ruling, macro print, or news source.
- Time risk: annualized edge can be attractive while absolute edge is still too
  small to survive spread and slippage.
- Model risk: a high `p_true` estimate is not tradable unless calibration and
  baseline comparison remain healthy.

## Stress framing

For open positions, state:

- worst case if every open position resolves against the portfolio
- concentration by theme or event
- exposure as a fraction of bankroll
- whether the circuit breaker would block another order

Do not recommend increasing exposure when the risk gate already says hold.

pi.dev may be used as an optional chat/coding harness through MCP. It should
surface risk evidence and tool outputs, not replace deterministic risk gates.
