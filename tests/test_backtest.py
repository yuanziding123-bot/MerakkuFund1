"""Historical replay runner — PIT slicing (fraction-based) + resolved-market records.

Scoring correctness lives in test_alpha.py; here we test that the runner builds
the right point-in-time records and skips the right markets.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from polyagents.dataflows.types import Candle
from polyagents.lab.backtest import BacktestRunner, momentum_signal, naive_signal

_T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _series(prices):
    return [Candle(ts=_T0 + timedelta(hours=i), open=p, high=p, low=p, close=p, volume=0.0)
            for i, p in enumerate(prices)]


def _mkt(token, price, question="crypto bitcoin market"):
    return SimpleNamespace(token_id=token, outcome="YES", question=question, price=price)


class _FakeClient:
    def __init__(self, hist):
        self.hist = hist
    def fetch_price_history(self, token_id, **k):
        return self.hist.get(token_id, [])


def _run(hist, markets, **kw):
    kw.setdefault("signal_fn", naive_signal)
    return BacktestRunner(client=_FakeClient(hist), **kw).replay(markets=markets)


def test_builds_one_record_per_resolved_market():
    s = _series([0.5] * 10)
    out = _run({"won": s, "lost": s}, [_mkt("won", 1.0), _mkt("lost", 0.0)])
    assert out["n_markets"] == 2
    assert {r["won"] for r in out["records"]} == {True, False}
    assert all(r["market_price"] == 0.5 for r in out["records"])


def test_naive_signal_ties_the_market_price():
    out = _run({"t": _series([0.42] * 10)}, [_mkt("t", 1.0)])
    r = out["records"][0]
    assert r["p_true"] == r["market_price"] == 0.42


def test_pit_uses_only_candles_before_prediction_time():
    # first half 0.30, second half jumps to 0.99; frac=0.5 must ignore the future
    out = _run({"t": _series([0.30] * 5 + [0.99] * 5)}, [_mkt("t", 1.0)])
    assert out["records"][0]["market_price"] == 0.30      # not 0.99 (no leakage)


def test_too_little_history_is_skipped():
    out = _run({"t": _series([0.5] * 4)}, [_mkt("t", 1.0)])   # n < min_history+1
    assert out["n_markets"] == 0


def test_unresolved_market_is_skipped():
    out = _run({"t": _series([0.5] * 10)}, [_mkt("t", 0.5)])  # price 0.5 = not resolved
    assert out["n_markets"] == 0


def test_max_markets_caps_the_run():
    s = _series([0.5] * 10)
    hist = {f"m{i}": s for i in range(10)}
    markets = [_mkt(f"m{i}", 1.0) for i in range(10)]
    out = BacktestRunner(client=_FakeClient(hist), signal_fn=naive_signal,
                         max_markets=3).replay(markets=markets)
    assert out["n_markets"] == 3


def test_momentum_signal_moves_with_trend():
    rising = _series([0.40, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61, 0.64, 0.67])
    out = BacktestRunner(client=_FakeClient({"t": rising}),
                         signal_fn=momentum_signal).replay(markets=[_mkt("t", 1.0)])
    r = out["records"][0]
    assert r["p_true"] > r["market_price"]
