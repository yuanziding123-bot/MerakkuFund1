# polyagents

A LangGraph multi-agent framework for **Polymarket** prediction markets, built
from scratch. The architecture mirrors
[TradingAgents](../TradingAgents) — a shared state ("blackboard") flows through
graph nodes, each node reads it, does its job, and writes a partial update back
— while the data logic is adapted from the proven
[polymarket](../) reference implementation.

The project is built layer by layer. **Layer 1** (data collection) gathers
everything about one market into a typed state; **Layer 2** (decision engine)
turns that into a sized, risk-gated trade. The execution + feedback layers come
next.

```
   ── Layer 1: data collection (deterministic) ──┐  ┌── Layer 2: decision engine ──
START ► market_data ► orderbook ► trades_flow ► news ► features ► signal ► decision ► reflection ► END
          price/        L2 micro-   buy/sell      (+senti-  factor    LLM       Kelly +    LLM self-
          volume        structure   flow          ment)     vector    p_true    risk gate  critique
```

`collect(market)` runs Layer 1 only (no LLM/keys); `analyze(market)` runs the
full pipeline (Layer 2 needs an Anthropic key, or inject an `llm`).

### Layer 1 capabilities (tracking the Merakku v3.0 Layer 1 projects)

| Source project | What we built | Module |
|---|---|---|
| **Polymarket py-clob-client** (P0) | Order book read via the **official CLOB SDK** (richer L2 depth), public REST `/book` as fallback | `dataflows/polymarket_client.py` |
| **MarketLens** (P0) | L2 microstructure: size-weighted micro-price, multi-level depth imbalance, book pressure, spread (bps), queue-at-touch | `dataflows/microstructure.py` |
| **FinGPT** (P0) | Sentiment scoring on news; `SentimentScorer` protocol + deterministic lexicon default, LLM/FinGPT pluggable | `dataflows/sentiment.py` |
| **Alpha DevBox** (P0) | Deterministic factor extraction — joins every collector's output into one named factor vector | `dataflows/features.py` |
| **Kronos** (P3) | `CandleForecaster` protocol seam over the close series; `NullForecaster` default | `dataflows/forecaster.py` |
| **Polyseer / poly_data** (P1) | planned — real-time market intelligence & event/historical retrieval | — |
| **pmxt / FinceptTerminal** (P2) | reference only — no code | — |

The FinGPT and Kronos seams are **injectable**: `PolyAgentsGraph(scorer=..., forecaster=...)`
swaps the lightweight built-ins for model-backed implementations without touching the graph.
The order book uses the official SDK by default (no keys needed for public L1 reads);
set `use_clob_sdk: False` in config to force the REST path.

### Layer 2 — decision engine (Merakku v3.0 three-agent architecture)

| Agent | Role | How | Module |
|---|---|---|---|
| **Signal** | factors + flow + sentiment → estimated true probability (`p_true`, direction, conviction) | LLM (Claude), structured output | `agents/signal_agent.py` |
| **Decision** | edge vs price → fractional-Kelly size + hard risk gates (liquidity, spread, edge floor) | **deterministic** (risk embedded, auditable) | `agents/decision_agent.py`, `agents/risk.py` |
| **Reflection** | pre-trade self-critique: risk flags, shaky assumptions, OOD | LLM (Claude), structured output | `agents/reflection_agent.py` |

The decision agent is intentionally **not** an LLM — sizing and risk are math
(`edge = p_true − price`, `f* = (q−p)/(1−p)`, quarter-Kelly capped at 5% of
bankroll, 6% edge floor; constants mirror the polymarket reference repo). The
`llm` is injectable: `PolyAgentsGraph(llm=...)`, and tests use a fake LLM so the
whole pipeline runs without a key or network.

```python
from polyagents.graph.orchestrator import PolyAgentsGraph
ta = PolyAgentsGraph()                 # needs ANTHROPIC_API_KEY for analyze()
market = ta.most_active_market()
state = ta.analyze(market)             # signal -> decision -> reflection
print(state["decision_report"])
```

### Polymarket docs MCP

The official [Polymarket documentation MCP](https://docs.polymarket.com/mcp) (a
docs **search/read** server — not a market-data feed) is wired in two ways:

- **Dev-time** — [`.mcp.json`](.mcp.json) registers it with Claude Code so the
  coding agent can look up Polymarket API/contract details while building polyagents.
- **Run-time** — `polyagents/mcp_tools.py` turns it into LangGraph tools via
  `langchain-mcp-adapters`, for the later decision-layer agents:

  ```python
  from polyagents.mcp_tools import load_mcp_tools_sync
  tools = load_mcp_tools_sync()   # [search_polymarket_documentation, query_docs_filesystem...]
  ```

  Servers are configured under `mcp_servers` in `default_config.py`; an empty map
  short-circuits with no network call and no extra imports.

## Layout

```
polyagents/
  default_config.py        # config dict + env overrides (mirrors TA default_config)
  dataflows/               # the data interface — "tools" the graph calls
    polymarket_client.py   # Gamma + data-api over httpx; order book via official py-clob-client SDK
    news.py                # Tavily news search (graceful no-key fallback)
    volume.py              # rebuild candle volume from /trades
    microstructure.py      # MarketLens-inspired L2 features
    sentiment.py           # FinGPT-inspired sentiment scorer (pluggable)
    forecaster.py          # Kronos-inspired CandleForecaster seam
    features.py            # Alpha DevBox-inspired factor join
    interface.py           # high-level fetch+format functions (report + structured data)
    types.py               # Market / Candle / OrderBook domain types
  agents/                  # Layer 2 — decision engine
    schemas.py             # Signal / Reflection (pydantic) + TradeDecision
    signal_agent.py        # LLM: estimate true probability
    decision_agent.py      # deterministic: edge + Kelly + risk gates
    risk.py                # pure risk math (edge, Kelly fraction)
    reflection_agent.py    # LLM: pre-trade self-critique
  mcp_tools.py             # load configured MCP servers (Polymarket docs) as LangGraph tools
  graph/
    state.py               # MarketState TypedDict (L1+L2 fields) + initial-state builder
    data_collection.py     # collector node factories (incl. features join)
    setup.py               # build_data_collection_graph (L1) + build_analysis_graph (L1+L2)
    orchestrator.py        # PolyAgentsGraph — collect() (L1) / analyze() (L1+L2)
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
