"""Historical data ingestion for Lab replay."""

__all__ = [
    "HistoricalCollectionsIngestor",
    "IngestionStats",
    "SettledMarket",
    "build_historical_collection",
    "build_historical_collections",
    "parse_settled_binary_market",
    "parse_settled_outcome_markets",
]


def __getattr__(name):
    if name in {"HistoricalCollectionsIngestor", "IngestionStats"}:
        from .polymarket_ingest import HistoricalCollectionsIngestor, IngestionStats

        return {"HistoricalCollectionsIngestor": HistoricalCollectionsIngestor, "IngestionStats": IngestionStats}[name]
    if name in {
        "SettledMarket",
        "build_historical_collection",
        "build_historical_collections",
        "parse_settled_binary_market",
        "parse_settled_outcome_markets",
    }:
        from .replay_builder import (
            SettledMarket,
            build_historical_collection,
            build_historical_collections,
            parse_settled_binary_market,
            parse_settled_outcome_markets,
        )

        return {
            "SettledMarket": SettledMarket,
            "build_historical_collection": build_historical_collection,
            "build_historical_collections": build_historical_collections,
            "parse_settled_binary_market": parse_settled_binary_market,
            "parse_settled_outcome_markets": parse_settled_outcome_markets,
        }[name]
    raise AttributeError(name)
