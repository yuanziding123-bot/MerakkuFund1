"""PolyAgentsGraph — the run entrypoint for the data-collection layer.

Wires up the read-only clients, compiles the graph once, and exposes
``collect()`` to run it for a single market. Mirrors TradingAgents'
``TradingAgentsGraph`` shape (build once, run per target) so the decision /
reflection layers can hang off the same object later.

Run a quick smoke test against the live, most-active market with:

    python -m polyagents
"""
from __future__ import annotations

from typing import Any

from polyagents.dataflows.news import NewsClient
from polyagents.dataflows.polymarket_client import PolymarketDataClient
from polyagents.dataflows.types import Market
from polyagents.dataflows.utils import utcnow
from polyagents.default_config import DEFAULT_CONFIG

from .setup import build_data_collection_graph
from .state import build_initial_state


class PolyAgentsGraph:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or DEFAULT_CONFIG.copy()
        self.client = PolymarketDataClient.from_config(self.config)
        self.news_client = NewsClient(self.config.get("tavily_api_key"))
        self.graph = build_data_collection_graph(self.client, self.news_client, self.config)

    def collect(self, market: Market, as_of: str | None = None) -> dict[str, Any]:
        """Run the data-collection graph for one market; return the final state."""
        as_of = as_of or utcnow().isoformat()
        initial = build_initial_state(market, as_of)
        return self.graph.invoke(initial)

    def most_active_market(self) -> Market | None:
        """Discovery helper: the single most-active tradeable side right now."""
        raw = self.client.list_active_markets(limit=self.config["markets_limit"])
        markets = self.client.to_markets(raw)
        return markets[0] if markets else None

    def close(self) -> None:
        self.client.close()


def _format_state(state: dict[str, Any]) -> str:
    lines = [
        "=" * 70,
        state["market_context"],
        "=" * 70,
        f"\n[price]\n{state['price_report']}",
        f"\n[volume]\n{state['volume_report']}",
        f"\n[orderbook]\n{state['orderbook_report']}",
        f"\n[trades_flow]\n{state['trades_flow_report']}",
        f"\n[news]\n{state['news_report']}",
    ]
    return "\n".join(lines)


def main() -> None:
    ta = PolyAgentsGraph()
    try:
        market = ta.most_active_market()
        if market is None:
            print("No active markets returned by Gamma.")
            return
        state = ta.collect(market)
        print(_format_state(state))
    finally:
        ta.close()


if __name__ == "__main__":
    main()
