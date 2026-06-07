# polyagents

A LangGraph multi-agent framework for **Polymarket** prediction markets, built
from scratch. The architecture mirrors
[TradingAgents](../TradingAgents) — a shared state ("blackboard") flows through
graph nodes, each node reads it, does its job, and writes a partial update back
— while the data logic is adapted from the proven
[polymarket](../) reference implementation.

The project is being built layer by layer. **This commit is the data-collection
layer only**: deterministic nodes that gather everything about one market into a
typed state. Decision / risk / reflection agents come in later layers.

```
                         ┌──────────────── MarketState (blackboard) ───────────────┐
START ─► price+volume ─► orderbook ─► trades-flow ─► news ─► END
          collector       collector     collector    collector
            │                │             │            │
        price_report     orderbook_     trades_flow_  news_report
        volume_report      report         report
```

## Layout

```
polyagents/
  default_config.py        # config dict + env overrides (mirrors TA default_config)
  dataflows/               # the data interface — "tools" the graph calls
    polymarket_client.py   # Gamma + CLOB + data-api, read-only over httpx
    news.py                # Tavily news search (graceful no-key fallback)
    volume.py              # rebuild candle volume from /trades
    interface.py           # high-level fetch+format functions (report + structured data)
    types.py               # Market / Candle / OrderBook domain types
  graph/
    state.py               # MarketState TypedDict + initial-state builder
    data_collection.py     # collector node factories
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
