---
name: cross-market-arb
description: Hunt for alpha in Polymarket CRYPTO markets by comparing the live exchange spot price with Polymarket's implied probability. Use when the user wants to find mispriced/lagging crypto markets (e.g. "Will BTC be above $X?") or cross-market arbitrage opportunities.
---

# Cross-market arbitrage (crypto)

Your edge here is **information that already exists on the exchange but hasn't
repriced on Polymarket yet**. Polymarket crypto markets ("Will BTC be above
$110k by June 30?") must agree with the live spot — when spot moves and the
Polymarket price lags, that gap is the opportunity. You have both data sources:
exchange spot (`crypto_price`/`crypto_24h`/`crypto_klines`) and the Polymarket
market (`scan_markets`/`market_snapshot`).

## Workflow

1. **Find crypto markets** — `scan_markets`, keep the ones about a crypto asset
   with a price threshold ("above/below $X", "hit $X", "$X by <date>").
2. **Read the question** — extract the asset (BTC/ETH/SOL…), the strike `K`, the
   direction (above/below), and the expiry date.
3. **Get the spot** — `crypto_price(asset)` for the live price `S`; `crypto_24h`
   and `crypto_klines` for momentum and recent volatility.
4. **Estimate the true probability** of the side resolving YES:
   - If `S` is already well past `K` in the YES direction with little time left →
     `p_true` near 1 (or near 0 if past in the NO direction). The market price
     should reflect this; if it lags, that's the edge.
   - If `S` is near `K`, weigh distance-to-strike against time-to-expiry and the
     recent volatility (a 2% daily-vol asset 5% from strike with 1 day left is
     very unlikely to cross; with 30 days, plausible). Be explicit about this.
   - State `S`, `K`, time left, and your reasoning in one or two sentences.
5. **Compare & size** — `market_snapshot` for the Polymarket price + microstructure;
   then `size_position(p_true, token)`. The deterministic gate (calibration toward
   the market, edge floor, APY, liquidity/spread) decides buy / hold / sell.
6. **Execute (paper)** — only on buy/sell: `paper_execute`. Then `portfolio_status`.

## Discipline
- This is a **signal, not certainty**: spot can reverse, and resolution rules /
  oracle timing matter. Respect every risk gate; never override a `hold`.
- Crypto markets move fast — note the time-to-expiry; a far-dated market may show
  edge but lock capital (the APY gate handles this).
- Never invent a spot price or a strike — only use what the tools return and what
  the question literally says. If you can't parse the strike, say so.
- Default to paper. Surface the spot vs implied-probability gap explicitly so the
  user can see the thesis.
