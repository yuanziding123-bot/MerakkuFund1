"""Data-collection layer: clients, types, and the high-level fetch interface."""
from __future__ import annotations

from .interface import (
    fetch_enriched_candles,
    format_price_report,
    format_volume_report,
    get_market_context,
    get_news_report,
    get_orderbook_report,
    get_trades_flow_report,
)
from .news import NewsClient, NewsItem
from .polymarket_client import PolymarketDataClient
from .types import Candle, Market, OrderBook, OrderBookLevel

__all__ = [
    "PolymarketDataClient",
    "NewsClient",
    "NewsItem",
    "Market",
    "Candle",
    "OrderBook",
    "OrderBookLevel",
    "get_market_context",
    "fetch_enriched_candles",
    "format_price_report",
    "format_volume_report",
    "get_orderbook_report",
    "get_trades_flow_report",
    "get_news_report",
]
