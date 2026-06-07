"""The shared state — polyagents' blackboard.

Like TradingAgents' ``AgentState``, a single ``MarketState`` flows through every
node. Nodes read the fields they need and return a partial dict that LangGraph
merges back in. Extending ``MessagesState`` gives us a reducer-backed
``messages`` list for free, so the later LLM analyst layer can drop in without a
state change.

The data-collection layer fills the ``*_report`` strings (for humans / LLMs) and
the ``raw`` dict (structured numbers for detectors / sizing).
"""
from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph import MessagesState

from polyagents.dataflows.interface import get_market_context
from polyagents.dataflows.types import Market


class MarketState(MessagesState):
    # --- target identity (resolved once at run start) ---
    market_id: Annotated[str, "Gamma market id under analysis"]
    condition_id: Annotated[str, "On-chain condition id"]
    token_id: Annotated[str, "CLOB token id for the analysed outcome side"]
    question: Annotated[str, "Market question"]
    outcome: Annotated[str, "Analysed side: YES or NO"]
    market_price: Annotated[float, "Last market price for the analysed side"]
    as_of: Annotated[str, "ISO timestamp the collection run was anchored to"]
    market_context: Annotated[str, "Deterministic market identity block"]

    # --- data-collection reports (filled by collector nodes) ---
    price_report: Annotated[str, "Price-history summary"]
    volume_report: Annotated[str, "Reconstructed-volume summary"]
    orderbook_report: Annotated[str, "Order-book depth / spread summary"]
    trades_flow_report: Annotated[str, "Buy/sell flow-imbalance summary"]
    news_report: Annotated[str, "Relevant news summary"]

    # --- structured numbers, keyed by source (e.g. raw["price"], raw["orderbook"]) ---
    raw: Annotated[dict[str, Any], "Structured numeric outputs from each collector"]


def build_initial_state(market: Market, as_of: str) -> dict[str, Any]:
    """Seed a fresh ``MarketState`` from a resolved :class:`Market`."""
    return {
        "messages": [],
        "market_id": market.market_id,
        "condition_id": market.condition_id,
        "token_id": market.token_id,
        "question": market.question,
        "outcome": market.outcome,
        "market_price": market.price,
        "as_of": as_of,
        "market_context": get_market_context(market),
        "price_report": "",
        "volume_report": "",
        "orderbook_report": "",
        "trades_flow_report": "",
        "news_report": "",
        "raw": {},
    }
