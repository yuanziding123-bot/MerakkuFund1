"""Assemble the data-collection StateGraph.

START → market_data → orderbook → trades_flow → news → features → END

Collectors run sequentially (like TradingAgents' analyst chain). Each does a
read-modify-write on ``state["raw"]``, so a sequential chain keeps those merges
conflict-free; the per-source reports are independent and could be parallelised
later behind a custom reducer if latency matters. ``features`` runs last so it
sees every populated source (the Alpha DevBox-style join point).
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from polyagents.dataflows.forecaster import CandleForecaster
from polyagents.dataflows.news import NewsClient
from polyagents.dataflows.polymarket_client import PolymarketDataClient
from polyagents.dataflows.sentiment import SentimentScorer

from .data_collection import (
    create_features_collector,
    create_market_data_collector,
    create_news_collector,
    create_orderbook_collector,
    create_trades_flow_collector,
)
from .state import MarketState

# Order here defines the execution chain.
_COLLECTOR_CHAIN = ["market_data", "orderbook", "trades_flow", "news", "features"]


def build_data_collection_graph(
    client: PolymarketDataClient,
    news_client: NewsClient,
    config: dict,
    scorer: SentimentScorer | None = None,
    forecaster: CandleForecaster | None = None,
):
    """Return a compiled LangGraph that fills a ``MarketState`` with market data.

    ``scorer`` (FinGPT seam) and ``forecaster`` (Kronos seam) are injectable;
    they default to the lightweight built-ins inside the node factories.
    """
    nodes = {
        "market_data": create_market_data_collector(client, config),
        "orderbook": create_orderbook_collector(client, config),
        "trades_flow": create_trades_flow_collector(client, config),
        "news": create_news_collector(news_client, config, scorer=scorer),
        "features": create_features_collector(forecaster=forecaster),
    }

    workflow = StateGraph(MarketState)
    for name in _COLLECTOR_CHAIN:
        workflow.add_node(name, nodes[name])

    workflow.add_edge(START, _COLLECTOR_CHAIN[0])
    for prev, nxt in zip(_COLLECTOR_CHAIN, _COLLECTOR_CHAIN[1:]):
        workflow.add_edge(prev, nxt)
    workflow.add_edge(_COLLECTOR_CHAIN[-1], END)

    return workflow.compile()
