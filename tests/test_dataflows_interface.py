"""Unit tests for the dataflows interface (fetch + format)."""
from __future__ import annotations

from polyagents.dataflows.interface import (
    fetch_enriched_candles,
    format_price_report,
    format_volume_report,
    get_market_context,
    get_news_report,
    get_orderbook_report,
    get_trades_flow_report,
)
from polyagents.dataflows.news import NewsClient

from .conftest import CONDITION, TOKEN


def test_market_context_mentions_question_and_side(sample_market):
    ctx = get_market_context(sample_market)
    assert "Will it rain tomorrow?" in ctx
    assert "YES" in ctx
    assert sample_market.condition_id in ctx


def test_price_report_summarises_trend(fake_client):
    candles = fake_client.fetch_price_history(TOKEN)
    text, data = format_price_report(candles)
    assert data["n_candles"] == 4
    assert data["last_price"] > data["first_price"]   # rising series
    assert data["pct_change"] > 0
    assert "Price history" in text


def test_volume_reconstructed_from_trades(fake_client):
    candles = fetch_enriched_candles(fake_client, TOKEN, CONDITION)
    text, data = format_volume_report(candles)
    # 100 + 50 + 30 token units land in-window; the other-asset trade is excluded.
    assert data["total_volume"] == 180.0
    assert data["bars_with_volume"] >= 1


def test_orderbook_report_computes_imbalance(fake_client):
    text, data = get_orderbook_report(fake_client, TOKEN)
    assert data["available"] is True
    assert data["best_bid"] == 0.44
    assert data["best_ask"] == 0.46
    assert data["mid"] == 0.45
    # bid depth 300 vs ask depth 200 -> positive imbalance
    assert data["depth_imbalance"] > 0


def test_trades_flow_imbalance_and_token_filter(fake_client):
    text, data = get_trades_flow_report(fake_client, CONDITION, TOKEN, lookback_hours=24)
    assert data["n_trades"] == 3          # the tok_no_9999 trade is filtered out
    assert data["n_buys"] == 2
    assert data["n_sells"] == 1
    assert data["flow_imbalance"] > 0     # buy notional dominates
    assert "Trade flow" in text


def test_news_disabled_without_key(sample_market):
    news = NewsClient(api_key=None)
    text, data = get_news_report(news, sample_market.question)
    assert data["enabled"] is False
    assert "disabled" in text.lower()
