---
name: market-data
description: Market data and microstructure research for Polymarket markets. Use when the user needs market snapshots, order-book pressure, liquidity, spread, flow, or similar-market context before analysis or trading.
source: HKUDS/Vibe-Trading agent/src/skills/market-microstructure
---

# Market data

Use this skill to ground a Polymarket question in observable market data before
making any claim about edge. This adapts Vibe-Trading's market-microstructure
discipline to MerakkuFund's prediction-market tools.

## Workflow

1. Use `scan_markets(limit, min_volume_24h)` to find liquid candidates.
2. Use `market_snapshot(token_id)` for the full evidence pack:
   - market price, volume, liquidity, and expiry
   - order-book microstructure: mid, spread, micro-price, depth imbalance, book pressure
   - trade-flow imbalance and consolidated factors
3. Use `find_similar_markets(query, n)` when historical context matters.
4. Summarize only what the tools return. If liquidity, spread, or flow is weak,
   say that the read is fragile.

## AIHF v0.2 Object Flow

This skill starts in **Ask** mode and is read-only. Its output should be framed
as a `Market` object candidate. When the user wants validation, the agent should
create or reference a `Hypothesis` in Lab rather than jumping straight to a
trade.

Canonical flow:

```text
Market -> Hypothesis -> Strategy -> Position -> Portfolio
```

For MarketLens-style research, use:

```text
Market -> Hypothesis -> Backtest -> Risk Evaluation -> EvaluationReport -> Promotion Recommendation
```

Every object reference should carry a point-in-time `snapshotId`, state, and
lineage. The agent may recommend promotion, but must not mutate state without an
explicit gate.

## Interpretation

- Tight spread plus balanced depth: cleaner price discovery, lower execution drag.
- Strong bid pressure plus ask-side thinness: possible upside pressure.
- Strong ask pressure plus weak bid depth: possible downside or exit pressure.
- Wide spread, thin book, or low volume: avoid over-interpreting the price.

## Guardrails

- This skill is read-only. Do not execute trades here.
- Do not forecast events from vibes. Explain the market state and the quality of
  the data.
- For action, switch to `polymarket-trading` so sizing and paper execution go
  through `size_position` and `paper_execute`.
- pi.dev, if used, is only an optional Ask/Lab chat or coding harness connected
  through MCP. It is not the financial engine and must not bypass the object
  flow or promotion gates.
