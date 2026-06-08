"""Data-collection nodes.

Each factory binds the clients/config and returns a ``node(state) -> dict``.
Nodes read the target identity from ``state`` (set by ``build_initial_state``),
fetch via the dataflows interface, and return a partial update: a ``*_report``
string plus a structured entry under ``raw``.

Mirrors TradingAgents' analyst-node factory pattern, but these collectors are
deterministic (no LLM) — that is the whole point of a data layer.
"""
from __future__ import annotations

from typing import Any, Callable

from polyagents.dataflows.features import extract_features, format_features_report
from polyagents.dataflows.forecaster import CandleForecaster, NullForecaster
from polyagents.dataflows.interface import (
    fetch_enriched_candles,
    format_price_report,
    format_volume_report,
    get_news_report,
    get_orderbook_report,
    get_trades_flow_report,
)
from polyagents.dataflows.news import NewsClient
from polyagents.dataflows.polymarket_client import PolymarketDataClient
from polyagents.dataflows.sentiment import LexiconSentimentScorer, SentimentScorer

Node = Callable[[dict], dict]


def create_market_data_collector(client: PolymarketDataClient, config: dict) -> Node:
    """Price history + reconstructed volume in one node (volume needs the candles)."""

    def node(state: dict) -> dict[str, Any]:
        candles = fetch_enriched_candles(
            client,
            token_id=state["token_id"],
            condition_id=state["condition_id"],
            interval=config["price_interval"],
            fidelity=config["price_fidelity"],
        )
        price_text, price_data = format_price_report(candles)
        volume_text, volume_data = format_volume_report(candles)
        raw = dict(state.get("raw", {}))
        raw["price"] = price_data
        raw["volume"] = volume_data
        return {"price_report": price_text, "volume_report": volume_text, "raw": raw}

    return node


def create_orderbook_collector(client: PolymarketDataClient, config: dict) -> Node:
    def node(state: dict) -> dict[str, Any]:
        text, data = get_orderbook_report(client, state["token_id"])
        raw = dict(state.get("raw", {}))
        raw["orderbook"] = data
        return {"orderbook_report": text, "raw": raw}

    return node


def create_trades_flow_collector(client: PolymarketDataClient, config: dict) -> Node:
    def node(state: dict) -> dict[str, Any]:
        text, data = get_trades_flow_report(
            client,
            condition_id=state["condition_id"],
            token_id=state["token_id"],
            lookback_hours=config["trades_lookback_hours"],
        )
        raw = dict(state.get("raw", {}))
        raw["trades_flow"] = data
        return {"trades_flow_report": text, "raw": raw}

    return node


def create_news_collector(
    news_client: NewsClient,
    config: dict,
    scorer: SentimentScorer | None = None,
) -> Node:
    """News + FinGPT-inspired sentiment. ``scorer`` defaults to the lexicon scorer."""
    scorer = scorer or LexiconSentimentScorer()

    def node(state: dict) -> dict[str, Any]:
        text, data = get_news_report(
            news_client, state["question"], max_results=config["news_max_results"], scorer=scorer
        )
        raw = dict(state.get("raw", {}))
        raw["news"] = data
        return {"news_report": text, "raw": raw}

    return node


def create_features_collector(forecaster: CandleForecaster | None = None) -> Node:
    """Alpha DevBox-inspired join: consolidate all collector outputs into factors.

    Runs last in the chain so every ``raw`` source is populated. Calls the
    (Kronos) forecaster hook on the close series; a real forecaster adds a
    ``forecast`` block, the default ``NullForecaster`` adds nothing.
    """
    forecaster = forecaster or NullForecaster()

    def node(state: dict) -> dict[str, Any]:
        raw = dict(state.get("raw", {}))
        data = extract_features(raw, forecaster=forecaster)
        raw["features"] = data
        return {"features_report": format_features_report(data), "raw": raw}

    return node
