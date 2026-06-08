"""Data-collection layer: clients, types, and the high-level fetch interface."""
from __future__ import annotations

from .features import extract_features, format_features_report
from .forecaster import CandleForecaster, NullForecaster
from .interface import (
    fetch_enriched_candles,
    format_price_report,
    format_volume_report,
    get_market_context,
    get_news_report,
    get_orderbook_report,
    get_trades_flow_report,
)
from .microstructure import compute_microstructure, format_microstructure_report
from .news import NewsClient, NewsItem
from .polymarket_client import PolymarketDataClient
from .sentiment import LexiconSentimentScorer, SentimentScorer, aggregate_sentiment
from .types import Candle, Market, OrderBook, OrderBookLevel

__all__ = [
    # clients & types
    "PolymarketDataClient",
    "NewsClient",
    "NewsItem",
    "Market",
    "Candle",
    "OrderBook",
    "OrderBookLevel",
    # interface
    "get_market_context",
    "fetch_enriched_candles",
    "format_price_report",
    "format_volume_report",
    "get_orderbook_report",
    "get_trades_flow_report",
    "get_news_report",
    # microstructure (MarketLens)
    "compute_microstructure",
    "format_microstructure_report",
    # sentiment (FinGPT)
    "SentimentScorer",
    "LexiconSentimentScorer",
    "aggregate_sentiment",
    # features (Alpha DevBox)
    "extract_features",
    "format_features_report",
    # forecaster (Kronos)
    "CandleForecaster",
    "NullForecaster",
]
