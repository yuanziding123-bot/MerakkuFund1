"""Ingest resolved Polymarket markets into Lab historical collections."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from polyagents.dataflows.polymarket_client import PolymarketDataClient
from polyagents.default_config import DEFAULT_CONFIG
from polyagents.storage.db import DataStore

from .replay_builder import build_historical_collection, parse_settled_binary_market


@dataclass
class IngestionStats:
    fetched_markets: int = 0
    inserted: int = 0
    duplicates: int = 0
    skipped_no_outcome: int = 0
    skipped_no_price_history: int = 0
    skipped_pit: int = 0
    skipped_non_binary: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def bump(self, reason: str | None) -> None:
        if reason and hasattr(self, reason):
            setattr(self, reason, getattr(self, reason) + 1)


class HistoricalCollectionsIngestor:
    """Build PIT-safe settled-market collections for ``BacktestRunner.run``."""

    def __init__(self, *, client, store: DataStore) -> None:
        self.client = client
        self.store = store

    def run(
        self,
        *,
        limit: int = 100,
        min_history: int = 4,
        prediction_policy: str = "midpoint",
    ) -> IngestionStats:
        stats = IngestionStats()
        raw_markets = self.client.list_resolved_markets(limit=limit)
        stats.fetched_markets = len(raw_markets)
        for raw in raw_markets:
            market, reason = parse_settled_binary_market(raw)
            if market is None:
                stats.bump(reason)
                continue
            candles = self.client.fetch_price_history(market.yes_token_id, interval="max")
            trades = self.client.fetch_market_trades(market.condition_id) if market.condition_id else []
            if trades:
                self.store.insert_trades(market.condition_id, trades)
            collection, reason = build_historical_collection(
                market,
                candles,
                trades=trades,
                min_history=min_history,
                prediction_policy=prediction_policy,
            )
            if collection is None:
                stats.bump(reason)
                continue
            if self.store.collection_exists(collection["token_id"], collection["as_of"]):
                stats.duplicates += 1
                continue
            self.store.record_market(market.to_market(), fetched_at=market.resolution_time.isoformat())
            self.store.record_collection(
                collection["token_id"],
                collection["as_of"],
                collection["question"],
                collection["market_price"],
                collection["raw"],
            )
            stats.inserted += 1
        return stats


def run_polymarket_ingestion(
    *,
    config: dict | None = None,
    db_path: str | None = None,
    limit: int = 100,
    min_history: int = 4,
    prediction_policy: str = "midpoint",
) -> IngestionStats:
    cfg = dict(config or DEFAULT_CONFIG)
    store = DataStore(db_path or cfg["db_path"])
    client = PolymarketDataClient.from_config(cfg)
    try:
        return HistoricalCollectionsIngestor(client=client, store=store).run(
            limit=limit,
            min_history=min_history,
            prediction_policy=prediction_policy,
        )
    finally:
        client.close()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest resolved Polymarket markets into Lab collections.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-history", type=int, default=4)
    parser.add_argument("--prediction-policy", default="midpoint")
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()
    stats = run_polymarket_ingestion(
        db_path=args.db_path,
        limit=args.limit,
        min_history=args.min_history,
        prediction_policy=args.prediction_policy,
    )
    print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
