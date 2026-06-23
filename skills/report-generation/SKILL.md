---
name: report-generation
description: Generate concise Polymarket research, backtest, execution, risk, and P&L reports from MerakkuFund tool outputs. Use when the user wants a structured Markdown report.
source: HKUDS/Vibe-Trading agent/src/skills/report-generate
---

# Report generation

Use this skill to turn MerakkuFund tool output into a structured Markdown report.
It adapts Vibe-Trading's professional report template to prediction-market
research and paper trading.

## Report structure

```markdown
# [Market or Strategy Report]

## Summary
- Decision: buy / sell / hold / research only
- Confidence: high / medium / low
- Key reason: one sentence

## Evidence
| Field | Value | Interpretation |
|---|---:|---|
| Market price | ... | ... |
| Liquidity | ... | ... |
| Spread | ... | ... |
| Flow / book pressure | ... | ... |

## Probability And Edge
- Estimated p_true:
- Market price:
- Calibrated edge:
- Sizing result:

## Execution And Risk
- Paper/live status:
- Position size:
- Portfolio exposure:
- Main risk gates:

## Review
- What would invalidate the trade:
- What to monitor next:
- Follow-up test:

---
Research only. Not investment advice.
```

## AIHF v0.2 EvaluationReport

When reporting a Lab backtest, always pair Markdown with a machine-readable
`EvaluationReport` JSON:

```json
{
  "hypothesisId": "hyp-...",
  "strategyId": "strat-...",
  "inputQuery": "bitcoin",
  "parameters": {},
  "marketCount": 0,
  "tradeCount": 0,
  "totalPnl": 0,
  "winRate": 0,
  "maxDrawdown": 0,
  "sharpe": 0,
  "profitFactor": 0,
  "riskRating": "High",
  "caveats": [],
  "promotionRecommendation": "remain_draft"
}
```

The Markdown summary should explain the recommendation, but the recommendation
itself must come from deterministic rules, not an LLM judgment.

## Rules

- Put the conclusion first.
- Every number must come from a tool result or an explicitly named assumption.
- Include uncertainty and at least three concrete risks for trade reports.
- For backtests, include sample size, period, hit rate or loss metrics, and
  whether the model beats the market baseline.
- End with a research-only disclaimer.
- If pi.dev is used as the chat shell, describe it as an optional MCP harness.
  Do not describe pi.dev as the AIHF core engine.
