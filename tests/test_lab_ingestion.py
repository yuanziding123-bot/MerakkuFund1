"""Historical settled-market ingestion for Lab replay."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polyagents.dataflows.types import Candle
from polyagents.dataflows.news import NewsItem
from polyagents.ingestion import (
    HistoricalCollectionsIngestor,
    build_historical_collection,
    parse_settled_binary_market,
)
from polyagents.ingestion.replay_builder import select_prediction_window
from polyagents.lab.backtest import BacktestRunner
from polyagents.lab.repository import LabRepository
from polyagents.lab.schemas import BacktestRequest, CreateHypothesisRequest
from polyagents.lab.service import create_hypothesis
from polyagents.storage.db import DataStore


_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _raw_market(**kw):
    raw = {
        "id": "m1",
        "conditionId": "cond1",
        "question": "Will bitcoin close above 100k?",
        "description": "desc",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["yes-token","no-token"]',
        "outcomePrices": '["1","0"]',
        "endDate": "2026-01-02T00:00:00Z",
        "volume24hr": 1000,
        "liquidityNum": 5000,
    }
    raw.update(kw)
    return raw


def _candles(n=10, *, start=_T0, price=0.5):
    return [
        Candle(
            ts=start + timedelta(hours=i),
            open=price + i * 0.01,
            high=price + i * 0.01,
            low=price + i * 0.01,
            close=price + i * 0.01,
            volume=0.0,
        )
        for i in range(n)
    ]


class _FakeClient:
    def __init__(self, markets, histories, trades=None):
        self.markets = markets
        self.histories = histories
        self.trades = trades or {}

    def list_resolved_markets(self, limit=100):
        return self.markets[:limit]

    def fetch_price_history(self, token_id, interval="max"):
        return self.histories.get(token_id, [])

    def fetch_market_trades(self, condition_id, min_ts=None, max_pages=25):
        return self.trades.get(condition_id, [])


class _FakeNewsClient:
    enabled = True

    def __init__(self, items):
        self.items = items
        self.calls = []

    def search_between(self, query, *, start, end, max_results=5):
        self.calls.append({"query": query, "start": start, "end": end, "max_results": max_results})
        return self.items[:max_results]


def _hypothesis(repo):
    return create_hypothesis(
        CreateHypothesisRequest(
            statement="Crypto news markets update slower than the model",
            category_filter="crypto",
            feature_set=["price_momentum"],
            prompt_version="signal-v1",
            model_version="claude-sonnet-4",
            lineage={"source": "test", "parents": []},
        ),
        repo=repo,
    )


def test_parse_resolved_binary_market_extracts_yes_outcome():
    market, reason = parse_settled_binary_market(_raw_market())

    assert reason is None
    assert market.yes_token_id == "yes-token"
    assert market.outcome == 1
    assert market.condition_id == "cond1"


def test_parse_resolved_binary_market_rejects_non_binary():
    market, reason = parse_settled_binary_market(_raw_market(outcomes='["A","B","C"]'))

    assert market is None
    assert reason == "skipped_non_binary"


def test_prediction_time_uses_midpoint_before_resolution():
    selected = select_prediction_window(
        _candles(10),
        resolution_time=_T0 + timedelta(days=1),
        min_history=4,
    )

    prediction_time, pit, market_price = selected
    assert prediction_time == _T0 + timedelta(hours=5)
    assert len(pit) == 5
    assert pit[-1].ts < prediction_time
    assert market_price == pit[-1].close


def test_collection_generation_shape_and_pit_metadata():
    market, _ = parse_settled_binary_market(_raw_market())
    collection, reason = build_historical_collection(market, _candles(10), min_history=4)

    assert reason is None
    assert collection["token_id"] == "yes-token"
    raw = collection["raw"]
    assert raw["lab"]["outcome"] == 1
    assert raw["lab"]["prediction_policy"] == "midpoint"
    assert raw["lab"]["available_at_max"] < collection["as_of"]
    assert "price_momentum" in raw["features"]["factors"]
    assert raw["orderbook"]["book_pressure"] == 0.0


def test_collection_generation_rebuilds_historical_trades_flow():
    market, _ = parse_settled_binary_market(_raw_market())
    trades = [
        {"asset": "yes-token", "timestamp": int((_T0 + timedelta(hours=2)).timestamp()), "size": 10, "price": 0.5, "side": "BUY"},
        {"asset": "yes-token", "timestamp": int((_T0 + timedelta(hours=3)).timestamp()), "size": 5, "price": 0.4, "side": "SELL"},
        {"asset": "yes-token", "timestamp": int((_T0 + timedelta(hours=8)).timestamp()), "size": 100, "price": 0.9, "side": "BUY"},
        {"asset": "no-token", "timestamp": int((_T0 + timedelta(hours=2)).timestamp()), "size": 99, "price": 0.2, "side": "BUY"},
    ]

    collection, reason = build_historical_collection(market, _candles(10), trades=trades, min_history=4)

    flow = collection["raw"]["trades_flow"]
    assert reason is None
    assert flow["source"] == "historical_trades"
    assert flow["n_trades"] == 2
    assert flow["n_buys"] == 1
    assert flow["n_sells"] == 1
    assert flow["buy_notional"] == 5.0
    assert flow["sell_notional"] == 2.0
    assert collection["raw"]["features"]["factors"]["flow_imbalance"] == flow["flow_imbalance"]


def test_collection_generation_adds_only_pit_safe_news_sentiment():
    market, _ = parse_settled_binary_market(_raw_market())
    news = _FakeNewsClient([
        NewsItem(
            title="Bitcoin wins support after bullish rally",
            url="https://example.com/old",
            snippet="strong gains confirmed",
            published="2026-01-01T03:00:00Z",
        ),
        NewsItem(
            title="Future warning",
            url="https://example.com/future",
            snippet="risk after prediction",
            published="2026-01-01T06:00:00Z",
        ),
        NewsItem(
            title="Undated bullish headline",
            url="https://example.com/undated",
            snippet="wins",
            published=None,
        ),
    ])

    collection, reason = build_historical_collection(
        market,
        _candles(10),
        news_client=news,
        min_history=4,
    )

    raw_news = collection["raw"]["news"]
    assert reason is None
    assert news.calls[0]["start"] == _T0
    assert news.calls[0]["end"] == _T0 + timedelta(hours=5)
    assert raw_news["n_items"] == 1
    assert raw_news["skipped_future"] == 1
    assert raw_news["skipped_no_published"] == 1
    assert raw_news["items"][0]["available_at"] == "2026-01-01T03:00:00Z"
    assert collection["raw"]["features"]["factors"]["sentiment"] > 0
    assert collection["raw"]["lab"]["available_at_max"] < collection["as_of"]


def test_collection_generation_treats_date_only_news_as_end_of_day():
    market, _ = parse_settled_binary_market(_raw_market())
    news = _FakeNewsClient([
        NewsItem(
            title="Bitcoin wins support",
            url="https://example.com/date",
            snippet="bullish",
            published="2026-01-01",
        ),
    ])

    collection, reason = build_historical_collection(
        market,
        _candles(10),
        news_client=news,
        min_history=4,
    )

    raw_news = collection["raw"]["news"]
    assert reason is None
    assert raw_news["n_items"] == 0
    assert raw_news["skipped_future"] == 1
    assert collection["raw"]["features"]["factors"]["sentiment"] == 0.0


def test_collection_generation_accepts_rfc_news_publish_dates():
    market, _ = parse_settled_binary_market(_raw_market())
    news = _FakeNewsClient([
        NewsItem(
            title="Bitcoin wins support",
            url="https://example.com/rfc",
            snippet="bullish",
            published="Thu, 01 Jan 2026 03:00:00 GMT",
        ),
    ])

    collection, reason = build_historical_collection(
        market,
        _candles(10),
        news_client=news,
        min_history=4,
    )

    raw_news = collection["raw"]["news"]
    assert reason is None
    assert raw_news["n_items"] == 1
    assert raw_news["items"][0]["available_at"] == "2026-01-01T03:00:00Z"


def test_collection_generation_skips_when_history_is_too_short():
    market, _ = parse_settled_binary_market(_raw_market())
    collection, reason = build_historical_collection(market, _candles(4), min_history=4)

    assert collection is None
    assert reason == "skipped_no_price_history"


def test_ingestion_inserts_and_deduplicates_collections(tmp_path):
    store = DataStore(tmp_path / "data.db")
    client = _FakeClient(
        [_raw_market()],
        {"yes-token": _candles(10)},
        {"cond1": [
            {"transactionHash": "h1", "asset": "yes-token", "timestamp": int((_T0 + timedelta(hours=2)).timestamp()), "size": 10, "price": 0.5, "side": "BUY"},
        ]},
    )
    ingestor = HistoricalCollectionsIngestor(client=client, store=store)

    first = ingestor.run(limit=10)
    second = ingestor.run(limit=10)

    assert first.inserted == 1
    assert second.duplicates == 1
    assert store.counts()["collections"] == 1
    assert store.counts()["trades"] == 1
    assert store.collection_exists("yes-token", "2026-01-01T05:00:00Z")
    store.close()


def test_ingestion_stats_include_historical_news_counts(tmp_path):
    store = DataStore(tmp_path / "data.db")
    client = _FakeClient([_raw_market()], {"yes-token": _candles(10)})
    news = _FakeNewsClient([
        NewsItem(
            title="Bitcoin wins support",
            url="https://example.com/old",
            snippet="bullish rally",
            published="2026-01-01T03:00:00Z",
        ),
        NewsItem(
            title="Future risk",
            url="https://example.com/future",
            snippet="warning",
            published="2026-01-01T09:00:00Z",
        ),
    ])

    stats = HistoricalCollectionsIngestor(client=client, store=store, news_client=news).run(limit=10)

    assert stats.inserted == 1
    assert stats.news_items_used == 1
    assert stats.news_items_skipped_future == 1
    store.close()


def test_ingestion_refreshes_duplicate_collections_with_news(tmp_path):
    store = DataStore(tmp_path / "data.db")
    client = _FakeClient([_raw_market()], {"yes-token": _candles(10)})

    first = HistoricalCollectionsIngestor(client=client, store=store).run(limit=10)
    news = _FakeNewsClient([
        NewsItem(
            title="Bitcoin wins support",
            url="https://example.com/old",
            snippet="bullish rally",
            published="2026-01-01T03:00:00Z",
        ),
    ])
    second = HistoricalCollectionsIngestor(client=client, store=store, news_client=news).run(limit=10)
    [collection] = store.fetch_collections()

    assert first.inserted == 1
    assert second.duplicates == 1
    assert second.updated_duplicates == 1
    assert second.inserted == 0
    assert collection["raw"]["news"]["n_items"] == 1
    assert collection["raw"]["features"]["factors"]["sentiment"] > 0
    assert store.counts()["collections"] == 1
    store.close()


def test_ingestion_stats_count_skips(tmp_path):
    store = DataStore(tmp_path / "data.db")
    client = _FakeClient(
        [
            _raw_market(outcomes='["A","B","C"]'),
            _raw_market(id="m2", conditionId="cond2", clobTokenIds='["yes2","no2"]'),
        ],
        {"yes2": []},
    )

    stats = HistoricalCollectionsIngestor(client=client, store=store).run(limit=10)

    assert stats.skipped_non_binary == 1
    assert stats.skipped_no_price_history == 1
    assert stats.inserted == 0
    store.close()


def test_backtest_runner_consumes_ingested_collections(tmp_path):
    store = DataStore(tmp_path / "data.db")
    repo = LabRepository(tmp_path / "lab.db")
    hypothesis = _hypothesis(repo)
    client = _FakeClient([_raw_market()], {"yes-token": _candles(10)})
    stats = HistoricalCollectionsIngestor(client=client, store=store).run(limit=10)

    result = BacktestRunner(store=store, repo=repo).run(
        BacktestRequest(
            hypothesis_id=hypothesis.id,
            time_window={
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-02T00:00:00Z",
            },
            market_filter={"category": "crypto", "settled_only": True},
            model_version="claude-sonnet-4",
            prompt_version="signal-v1",
            calibrator_id="shrink-to-market-v1",
        )
    )

    report = repo.get_report(result.report_id)
    assert stats.inserted == 1
    assert result.forecast_count == 1
    assert report["market_universe"]["source"] == "collections"
    assert report["data_quality"]["uses_fixture_data"] is False
    assert report["market_sample"][0]["snapshot_manifest"]["pit_status"] == "clean"
    store.close()
    repo.close()
