"""Assemble the polyagents StateGraphs.

Layer 1 (data collection):
    START → market_data → orderbook → trades_flow → news → features → END

Full analysis (Layer 1 + Layer 2 decision engine):
    … → features → signal → decision → reflection → END

Collectors run sequentially (like TradingAgents' analyst chain). Each does a
read-modify-write on ``state["raw"]``, so a sequential chain keeps those merges
conflict-free. ``features`` runs last among collectors so the signal agent sees
every populated source.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from polyagents.agents.decision_agent import create_decision_agent
from polyagents.agents.reflection_agent import create_reflection_agent
from polyagents.agents.signal_agent import create_signal_agent
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

_COLLECTOR_CHAIN = ["market_data", "orderbook", "trades_flow", "news", "features"]
_AGENT_CHAIN = ["signal", "decision", "reflection"]


def _collector_nodes(client, news_client, config, scorer, forecaster) -> dict:
    return {
        "market_data": create_market_data_collector(client, config),
        "orderbook": create_orderbook_collector(client, config),
        "trades_flow": create_trades_flow_collector(client, config),
        "news": create_news_collector(news_client, config, scorer=scorer),
        "features": create_features_collector(forecaster=forecaster),
    }


def _wire_chain(workflow: StateGraph, names: list[str]) -> None:
    """START → names[0] → … → names[-1] → END (linear)."""
    workflow.add_edge(START, names[0])
    for prev, nxt in zip(names, names[1:]):
        workflow.add_edge(prev, nxt)
    workflow.add_edge(names[-1], END)


def build_data_collection_graph(
    client: PolymarketDataClient,
    news_client: NewsClient,
    config: dict,
    scorer: SentimentScorer | None = None,
    forecaster: CandleForecaster | None = None,
):
    """Layer 1 only: fill a ``MarketState`` with market data.

    ``scorer`` (FinGPT seam) and ``forecaster`` (Kronos seam) are injectable.
    """
    nodes = _collector_nodes(client, news_client, config, scorer, forecaster)
    workflow = StateGraph(MarketState)
    for name in _COLLECTOR_CHAIN:
        workflow.add_node(name, nodes[name])
    _wire_chain(workflow, _COLLECTOR_CHAIN)
    return workflow.compile()


def build_analysis_graph(
    client: PolymarketDataClient,
    news_client: NewsClient,
    config: dict,
    llm,
    scorer: SentimentScorer | None = None,
    forecaster: CandleForecaster | None = None,
):
    """Layer 1 + Layer 2: data collection then signal → decision → reflection.

    ``llm`` drives the signal and reflection agents (decision is deterministic).
    """
    nodes = _collector_nodes(client, news_client, config, scorer, forecaster)
    nodes["signal"] = create_signal_agent(llm)
    nodes["decision"] = create_decision_agent(config)
    nodes["reflection"] = create_reflection_agent(llm)

    chain = _COLLECTOR_CHAIN + _AGENT_CHAIN
    workflow = StateGraph(MarketState)
    for name in chain:
        workflow.add_node(name, nodes[name])
    _wire_chain(workflow, chain)
    return workflow.compile()
