"""Cross-market crypto arbitrage — the cross-market-arb strategy as a loop capability.
Tests the market parser + normal CDF (pure) and the capability wiring (fake LLM)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import crypto_arb_capability
from polyagents.kernel.wiring import parse_crypto_market, _norm_cdf


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_parse_crypto_market():
    a = parse_crypto_market("Will BTC be above $110k by June 30?")
    assert a == {"asset": "BTC", "strike": 110000.0, "direction": "above"}
    b = parse_crypto_market("Will Ethereum be below $2,500 this week?")
    assert b == {"asset": "ETH", "strike": 2500.0, "direction": "below"}
    dip = parse_crypto_market("Will Bitcoin dip to $60,000 on July 3?")
    assert dip["direction"] == "below" and dip["strike"] == 60000.0          # 'dip to' = downward
    assert parse_crypto_market("Will Bitcoin reach $67,500 in July?")["direction"] == "above"
    assert parse_crypto_market("Will Spain win the 2026 World Cup?") is None   # not crypto


def test_norm_cdf_monotone_and_centered():
    assert abs(_norm_cdf(0.0) - 0.5) < 1e-9
    assert _norm_cdf(-3) < _norm_cdf(0) < _norm_cdf(3)
    assert _norm_cdf(3) > 0.99


def test_find_crypto_arb_capability_surfaces_best():
    def fn(query):
        opps = [
            {"question": "Will BTC be above $200k?", "asset": "BTC", "gap": 0.30,
             "spot": 65000, "strike": 200000, "direction": "above",
             "p_model": 0.02, "market_price": 0.32, "days": 30},
            {"question": "Will ETH be above $3k?", "asset": "ETH", "gap": 0.05,
             "spot": 3100, "strike": 3000, "direction": "above",
             "p_model": 0.7, "market_price": 0.65, "days": 10}]
        return {"query": query, "n": 2, "opportunities": opps, "best": opps[0]}

    llm = FakeLLM('{"action":"call","capability":"find_crypto_arb"}',
                  '{"action":"final","answer":"最大错价:BTC $200k"}')
    res = KernelController([crypto_arb_capability(fn)], llm).run("find mispriced crypto markets")
    assert [s.capability for s in res.trace] == ["find_crypto_arb"]
    arb = res.facts["crypto_arb"]
    assert arb["best"]["question"].startswith("Will BTC")     # biggest |gap| first
    assert arb["n"] == 2
