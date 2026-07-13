"""Ingest resolved Polymarket markets into Lab historical collections."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from polyagents.dataflows.polymarket_client import PolymarketDataClient
from polyagents.dataflows.news import NewsClient
from polyagents.default_config import DEFAULT_CONFIG
from polyagents.storage.db import DataStore

from .replay_builder import build_historical_collections, parse_settled_outcome_markets


@dataclass
class IngestionStats:
    fetched_markets: int = 0
    inserted: int = 0
    duplicates: int = 0
    updated_duplicates: int = 0
    skipped_no_outcome: int = 0
    skipped_no_price_history: int = 0
    skipped_pit: int = 0
    skipped_non_binary: int = 0
    news_items_used: int = 0
    news_items_skipped_no_published: int = 0
    news_items_skipped_future: int = 0
    news_cache_hits: int = 0
    news_cache_misses: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def bump(self, reason: str | None) -> None:
        if reason and hasattr(self, reason):
            setattr(self, reason, getattr(self, reason) + 1)


class HistoricalCollectionsIngestor:
    """Build PIT-safe settled-market collections for ``BacktestRunner.run``."""

    def __init__(self, *, client, store: DataStore, news_client=None) -> None:
        self.client = client
        self.store = store
        self.news_client = news_client

    def run(
        self,
        *,
        limit: int = 100,
        min_history: int = 4,
        news_max_results: int = 5,
        prediction_policy: str = "multi",
    ) -> IngestionStats:
        stats = IngestionStats()
        news_client = (
            _NewsSearchCache(self.news_client, stats)
            if self.news_client is not None and getattr(self.news_client, "enabled", False)
            else self.news_client
        )
        raw_markets = self.client.list_resolved_markets(limit=limit)
        stats.fetched_markets = len(raw_markets)
        for raw in raw_markets:
            markets, reason = parse_settled_outcome_markets(raw)
            if not markets:
                stats.bump(reason)
                continue
            condition_id = markets[0].condition_id
            trades = self.client.fetch_market_trades(condition_id) if condition_id else []
            if trades:
                self.store.insert_trades(condition_id, trades)
            for market in markets:
                candles = self.client.fetch_price_history(market.yes_token_id, interval="max")
                collections, reason = build_historical_collections(
                    market,
                    candles,
                    trades=trades,
                    news_client=news_client,
                    news_max_results=news_max_results,
                    min_history=min_history,
                    prediction_policy=prediction_policy,
                )
                if not collections:
                    stats.bump(reason)
                    continue
                for collection in collections:
                    news = (collection["raw"].get("news") or {})
                    stats.news_items_used += int(news.get("n_items") or 0)
                    stats.news_items_skipped_no_published += int(news.get("skipped_no_published") or 0)
                    stats.news_items_skipped_future += int(news.get("skipped_future") or 0)
                    if self.store.collection_exists(collection["token_id"], collection["as_of"]):
                        stats.duplicates += 1
                        stats.updated_duplicates += 1
                    else:
                        stats.inserted += 1
                    self.store.record_market(market.to_market(), fetched_at=market.resolution_time.isoformat())
                    self.store.record_collection(
                        collection["token_id"],
                        collection["as_of"],
                        collection["question"],
                        collection["market_price"],
                        collection["raw"],
                    )
        return stats


class _NewsSearchCache:
    """Memoize PIT news searches during one ingestion run."""

    def __init__(self, client, stats: IngestionStats) -> None:
        self.client = client
        self.stats = stats
        self._cache: dict[tuple[str, str, str, int], list] = {}

    @property
    def enabled(self) -> bool:
        return getattr(self.client, "enabled", False)

    def search_between(self, query, *, start, end, max_results=5):
        # Tavily's date-window API is date-granular. Cache by date range, then
        # let build_historical_news_sentiment apply the exact PIT timestamp.
        key = (
            str(query),
            start.date().isoformat() if hasattr(start, "date") else str(start),
            end.date().isoformat() if hasattr(end, "date") else str(end),
            int(max_results),
        )
        if key in self._cache:
            self.stats.news_cache_hits += 1
            return list(self._cache[key])
        self.stats.news_cache_misses += 1
        items = list(self.client.search_between(query, start=start, end=end, max_results=max_results))
        self._cache[key] = items
        return list(items)


def run_polymarket_ingestion(
    *,
    config: dict | None = None,
    db_path: str | None = None,
    limit: int = 100,
    min_history: int = 4,
    news_max_results: int | None = None,
    prediction_policy: str = "multi",
) -> IngestionStats:
    cfg = dict(config or DEFAULT_CONFIG)
    store = DataStore(db_path or cfg["db_path"])
    client = PolymarketDataClient.from_config(cfg)
    news_client = NewsClient(cfg.get("tavily_api_key"))
    try:
        return HistoricalCollectionsIngestor(client=client, store=store, news_client=news_client).run(
            limit=limit,
            min_history=min_history,
            news_max_results=news_max_results or int(cfg.get("news_max_results", 5)),
            prediction_policy=prediction_policy,
        )
    finally:
        client.close()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest resolved Polymarket markets into Lab collections.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-history", type=int, default=4)
    parser.add_argument("--news-max-results", type=int, default=None)
    parser.add_argument("--prediction-policy", default="multi")
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()
    stats = run_polymarket_ingestion(
        db_path=args.db_path,
        limit=args.limit,
        min_history=args.min_history,
        news_max_results=args.news_max_results,
        prediction_policy=args.prediction_policy,
    )
    print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
