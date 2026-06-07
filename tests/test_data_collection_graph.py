"""End-to-end test of the data-collection graph with a fake client."""
from __future__ import annotations

from polyagents.dataflows.news import NewsClient
from polyagents.default_config import DEFAULT_CONFIG
from polyagents.graph.setup import build_data_collection_graph
from polyagents.graph.state import build_initial_state


def test_graph_populates_all_reports(fake_client, sample_market):
    config = DEFAULT_CONFIG.copy()
    news = NewsClient(api_key=None)  # disabled -> graceful report, no network
    graph = build_data_collection_graph(fake_client, news, config)

    final = graph.invoke(build_initial_state(sample_market, as_of="2026-06-07T00:00:00+00:00"))

    # Every collector wrote its report...
    for key in ("price_report", "volume_report", "orderbook_report", "trades_flow_report", "news_report"):
        assert final[key], f"{key} should be non-empty"

    # ...and its structured numbers under raw.
    for key in ("price", "volume", "orderbook", "trades_flow", "news"):
        assert key in final["raw"], f"raw['{key}'] missing"

    # Spot-check that numbers flowed through, not just strings.
    assert final["raw"]["trades_flow"]["n_trades"] == 3
    assert final["raw"]["orderbook"]["mid"] == 0.45
    assert final["raw"]["volume"]["total_volume"] == 180.0
    assert final["market_context"]  # identity resolved at seed time


def test_initial_state_has_identity(sample_market):
    state = build_initial_state(sample_market, as_of="2026-06-07T00:00:00+00:00")
    assert state["token_id"] == sample_market.token_id
    assert state["question"] == sample_market.question
    assert state["raw"] == {}
