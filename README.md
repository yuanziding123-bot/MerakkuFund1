# polyagents

A LangGraph multi-agent framework for **Polymarket** prediction markets, built
from scratch. The architecture mirrors
[TradingAgents](../TradingAgents) — a shared state ("blackboard") flows through
graph nodes, each node reads it, does its job, and writes a partial update back
— while the data logic is adapted from the proven
[polymarket](../) reference implementation.

The project is being built layer by layer. **This is the data-collection layer
(Layer 1)**: deterministic nodes that gather everything about one market into a
typed state. Decision / risk / reflection agents come in later layers.

```
                              ┌──────────── MarketState (blackboard) ───────────┐
START ─► market_data ─► orderbook ─► trades_flow ─► news ─► features ─► END
          collector      collector    collector    collector  (join)
            │               │            │            │          │
        price_report    orderbook_   trades_flow_  news_report  features_report
        volume_report     report        report     (+sentiment) (factor vector)
```

### Layer 1 capabilities (built native, inspired by the Merakku doc's 5 projects)

| Source project | What we built | Module |
|---|---|---|
| **MarketLens** (P0) | L2 microstructure: size-weighted micro-price, multi-level depth imbalance, book pressure, spread (bps), queue-at-touch | `dataflows/microstructure.py` |
| **FinGPT** (P0) | Sentiment scoring on news; `SentimentScorer` protocol + deterministic lexicon default, LLM/FinGPT pluggable | `dataflows/sentiment.py` |
| **Alpha DevBox** (P0) | Deterministic factor extraction — joins every collector's output into one named factor vector | `dataflows/features.py` |
| **Kronos** (P3) | `CandleForecaster` protocol seam over the close series; `NullForecaster` default | `dataflows/forecaster.py` |
| **FinceptTerminal** (P2) | reference only — no code | — |

The FinGPT and Kronos seams are **injectable**: `PolyAgentsGraph(scorer=..., forecaster=...)`
swaps the lightweight built-ins for model-backed implementations without touching the graph.

## Layout

```
polyagents/
  default_config.py        # config dict + env overrides (mirrors TA default_config)
  dataflows/               # the data interface — "tools" the graph calls
    polymarket_client.py   # Gamma + CLOB + data-api, read-only over httpx
    news.py                # Tavily news search (graceful no-key fallback)
    volume.py              # rebuild candle volume from /trades
    microstructure.py      # MarketLens-inspired L2 features
    sentiment.py           # FinGPT-inspired sentiment scorer (pluggable)
    forecaster.py          # Kronos-inspired CandleForecaster seam
    features.py            # Alpha DevBox-inspired factor join
    interface.py           # high-level fetch+format functions (report + structured data)
    types.py               # Market / Candle / OrderBook domain types
  graph/
    state.py               # MarketState TypedDict + initial-state builder
    data_collection.py     # collector node factories (incl. features join)
    setup.py               # builds the data-collection StateGraph
    orchestrator.py        # PolyAgentsGraph — the run entrypoint
```

## Quick start

```powershell
# Uses the workspace venv (already provisioned at C:\polymarket\.venv)
C:\polymarket\.venv\Scripts\python.exe -m pip install -r requirements.txt
C:\polymarket\.venv\Scripts\python.exe -m pytest          # run from this folder

# Collect data for the most active market (read-only, no keys needed)
C:\polymarket\.venv\Scripts\python.exe -m polyagents
```

No API keys are required for the data layer (Gamma, prices-history, /trades and
the CLOB order book are all public read endpoints). Set `TAVILY_API_KEY` to
enable the news collector; without it the news report degrades gracefully.

## Design notes

- **Blackboard over message-passing.** Like TradingAgents, every node returns a
  dict that LangGraph merges into the single `MarketState`. Collectors are
  deterministic (no LLM) — they belong to the data layer, the LLM analyst
  agents read these reports later.
- **Reports carry both text and numbers.** Each collector writes a
  human-readable `*_report` string *and* structured numeric data into
  `state["raw"]`, because Polymarket's downstream (detectors, ML, sizing) needs
  the numbers, not just prose.
