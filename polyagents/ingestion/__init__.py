"""Historical data ingestion for Lab replay."""

__all__ = [
    "HistoricalCollectionsIngestor",
    "IngestionStats",
    "SettledMarket",
    "build_historical_collection",
    "parse_settled_binary_market",
]


def __getattr__(name):
    if name in {"HistoricalCollectionsIngestor", "IngestionStats"}:
        from .polymarket_ingest import HistoricalCollectionsIngestor, IngestionStats

        return {"HistoricalCollectionsIngestor": HistoricalCollectionsIngestor, "IngestionStats": IngestionStats}[name]
    if name in {"SettledMarket", "build_historical_collection", "parse_settled_binary_market"}:
        from .replay_builder import SettledMarket, build_historical_collection, parse_settled_binary_market

        return {
            "SettledMarket": SettledMarket,
            "build_historical_collection": build_historical_collection,
            "parse_settled_binary_market": parse_settled_binary_market,
        }[name]
    raise AttributeError(name)
