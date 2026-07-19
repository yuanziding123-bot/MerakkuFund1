"""Read-only Polymarket data client.

Reads four Polymarket surfaces:

  * Gamma REST       — active-market metadata (discovery)
  * CLOB REST        — ``/prices-history``
  * data-api REST    — ``/trades`` (taker/maker fills, for volume + flow)
  * Order book       — via the **official py-clob-client SDK** (Merakku v3.0
                       Layer 1 P0), falling back to the public ``/book`` REST
                       endpoint when the SDK isn't importable.

Per the v3.0 plan the official Python CLOB client replaces self-built API calls
for the order book; its public L1 reads (``get_order_book``) need no API keys,
so the layer still runs credential-free. Order *placement* (which does need
keys) stays out of this read-only client — that's the execution layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from .types import Candle, Market, OrderBook, OrderBookLevel, Outcome
from .utils import parse_iso, parse_json_field

# /trades is desc-by-timestamp. Docs claim offset max=10000 but production
# returns 400 once offset >= 3500, so cap at 3000 to stay safe.
TRADES_PAGE_SIZE = 500
TRADES_MAX_OFFSET = 3000

# Gamma caps each response at 100 markets regardless of the requested limit.
GAMMA_PAGE = 100


class PolymarketDataClient:
    """All methods are read-only and safe to call without credentials."""

    def __init__(
        self,
        gamma_base: str,
        clob_base: str,
        data_api_base: str,
        timeout: float = 20.0,
        http: httpx.Client | None = None,
        chain_id: int = 137,
        use_clob_sdk: bool = True,
        clob: Any | None = None,
    ) -> None:
        self.gamma_base = gamma_base.rstrip("/")
        self.clob_base = clob_base.rstrip("/")
        self.data_api_base = data_api_base.rstrip("/")
        self._http = http or httpx.Client(timeout=timeout)
        self.chain_id = chain_id
        self._use_clob_sdk = use_clob_sdk
        # Official py-clob-client handle. May be injected (tests) or lazily
        # constructed on first order-book read. ``_clob_ready`` guards re-init.
        self._clob = clob
        self._clob_ready = clob is not None

    @classmethod
    def from_config(cls, config: dict, http: httpx.Client | None = None) -> "PolymarketDataClient":
        return cls(
            gamma_base=config["gamma_base"],
            clob_base=config["clob_base"],
            data_api_base=config["data_api_base"],
            timeout=config.get("http_timeout", 20.0),
            http=http,
            chain_id=config.get("polymarket_chain_id", 137),
            use_clob_sdk=config.get("use_clob_sdk", True),
        )

    def _get_clob(self):
        """Lazily construct the official read-only CLOB SDK client (no keys)."""
        if self._clob_ready or not self._use_clob_sdk:
            return self._clob
        self._clob_ready = True
        try:
            from py_clob_client.client import ClobClient

            self._clob = ClobClient(host=self.clob_base, chain_id=self.chain_id)
        except Exception:
            self._clob = None
        return self._clob

    def close(self) -> None:
        self._http.close()

    # ----- market discovery (Gamma) -----------------------------------------

    def list_active_markets(self, limit: int = 500) -> list[dict]:
        """Page active, non-archived markets ordered by 24h volume (desc)."""
        out: list[dict] = []
        offset = 0
        while len(out) < limit:
            params = {
                "active": "true",
                "archived": "false",
                "closed": "false",
                "limit": min(GAMMA_PAGE, limit - len(out)),
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            }
            try:
                r = self._http.get(f"{self.gamma_base}/markets", params=params)
                r.raise_for_status()
                page = r.json() or []
            except Exception:
                break
            if not page:
                break
            out.extend(page)
            if len(page) < GAMMA_PAGE:
                break
            offset += GAMMA_PAGE
        return out

    def list_resolved_markets(self, limit: int = 200) -> list[dict]:
        """Page CLOSED (resolved) markets ordered by 24h volume — for backtests.

        Resolved markets carry their final ``outcomePrices`` (1.0 / 0.0), i.e. the
        realised outcome, so a historical replay knows who won.
        """
        out: list[dict] = []
        offset = 0
        while len(out) < limit:
            params = {
                "closed": "true",
                "archived": "false",
                "limit": min(GAMMA_PAGE, limit - len(out)),
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            }
            try:
                r = self._http.get(f"{self.gamma_base}/markets", params=params)
                r.raise_for_status()
                page = r.json() or []
            except Exception:
                break
            if not page:
                break
            out.extend(page)
            if len(page) < GAMMA_PAGE:
                break
            offset += GAMMA_PAGE
        return out

    def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text market search via Gamma's public-search — finds niche / low-volume
        markets (e.g. F1 'Safety Car?') that the volume-ordered listing never pages to.
        Returns raw Gamma market dicts (both open and resolved)."""
        if not query:
            return []
        try:
            r = self._http.get(f"{self.gamma_base}/public-search",
                               params={"q": query, "limit_per_type": min(limit, 100)})
            r.raise_for_status()
            events = (r.json() or {}).get("events", []) or []
        except Exception:
            return []
        out: list[dict] = []
        for ev in events:
            for m in ev.get("markets", []) or []:
                out.append(m)
                if len(out) >= limit:
                    return out
        return out

    def fetch_market_by_condition(self, condition_id: str) -> dict | None:
        """Fetch one market by condition id — for settlement.

        Gamma's ``condition_ids`` lookup defaults to active markets, so a
        resolved market needs an explicit ``closed=true`` retry. Try the open
        view first, then the closed view.
        """
        if not condition_id:
            return None
        for extra in ({}, {"closed": "true"}):
            try:
                r = self._http.get(
                    f"{self.gamma_base}/markets",
                    params={"condition_ids": condition_id, **extra},
                )
                r.raise_for_status()
                data = r.json() or []
            except Exception:
                continue
            if isinstance(data, list) and data:
                return data[0]
        return None

    def to_markets(self, raw_markets: Iterable[dict]) -> list[Market]:
        """Normalise Gamma payloads into one ``Market`` per outcome side."""
        out: list[Market] = []
        for m in raw_markets:
            try:
                outcomes = parse_json_field(m.get("outcomes"))
                prices = [float(p) for p in parse_json_field(m.get("outcomePrices") or [])]
                token_ids = parse_json_field(m.get("clobTokenIds") or [])
                if not outcomes or len(outcomes) != len(prices) or len(prices) != len(token_ids):
                    continue
                expiry = parse_iso(m.get("endDate") or m.get("end_date_iso"))
                if not expiry:
                    continue
                days_to_expiry = (expiry - datetime.now(timezone.utc)).total_seconds() / 86400.0
                for outcome_label, price, token_id in zip(outcomes, prices, token_ids):
                    side: Outcome = "YES" if str(outcome_label).strip().lower() in {"yes", "true"} else "NO"
                    out.append(
                        Market(
                            market_id=str(m.get("id") or m.get("conditionId")),
                            condition_id=str(m.get("conditionId") or ""),
                            question=str(m.get("question") or ""),
                            description=str(m.get("description") or ""),
                            outcome=side,
                            token_id=str(token_id),
                            price=price,
                            volume_24h=float(m.get("volume24hr") or 0.0),
                            liquidity=float(m.get("liquidityNum") or m.get("liquidity") or 0.0),
                            spread=float(m.get("spread") or 0.0),
                            days_to_expiry=days_to_expiry,
                            expiry=expiry,
                            raw=m,
                        )
                    )
            except Exception:
                continue
        return out

    # ----- price history (CLOB) ---------------------------------------------

    def fetch_price_history(self, token_id: str, interval: str = "1h", fidelity: int = 60) -> list[Candle]:
        """Synthetic candles (open=high=low=close=price); volume filled later.

        Low-activity / resolved markets return nothing at fine fidelity, so coarsen
        (hourly → 3h → 12h → daily) until the CLOB serves some history."""
        history: list = []
        tried = []
        for fid in (fidelity, 180, 720, 1440):
            if fid in tried:
                continue
            tried.append(fid)
            try:
                r = self._http.get(
                    f"{self.clob_base}/prices-history",
                    params={"market": token_id, "interval": interval, "fidelity": fid},
                )
                r.raise_for_status()
                history = r.json().get("history", [])
            except Exception:
                history = []
            if history:
                break
        candles: list[Candle] = []
        for point in history:
            try:
                ts = datetime.fromtimestamp(int(point["t"]), tz=timezone.utc)
                price = float(point["p"])
            except (KeyError, ValueError, TypeError):
                continue
            candles.append(Candle(ts=ts, open=price, high=price, low=price, close=price, volume=0.0))
        return candles

    # ----- trades (data-api) ------------------------------------------------

    def fetch_market_trades(self, condition_id: str, min_ts: int | None = None, max_pages: int = 25) -> list[dict]:
        """Page /trades (desc-by-timestamp) until ``min_ts`` or the offset cap."""
        if not condition_id:
            return []
        out: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            if offset > TRADES_MAX_OFFSET:
                break
            try:
                r = self._http.get(
                    f"{self.data_api_base}/trades",
                    params={
                        "market": condition_id,
                        "limit": TRADES_PAGE_SIZE,
                        "offset": offset,
                        "takerOnly": "false",
                    },
                )
                r.raise_for_status()
                page = r.json() or []
            except Exception:
                break
            if not page:
                break
            out.extend(page)
            oldest = page[-1].get("timestamp")
            if min_ts is not None and isinstance(oldest, (int, float)) and oldest < min_ts:
                break
            if len(page) < TRADES_PAGE_SIZE:
                break
            offset += TRADES_PAGE_SIZE
        return out

    # ----- order book (official SDK, REST fallback) -------------------------

    def fetch_order_book(self, token_id: str) -> OrderBook | None:
        """L2 snapshot via the official py-clob-client SDK, REST as fallback."""
        clob = self._get_clob()
        if clob is not None:
            book = self._order_book_via_sdk(clob, token_id)
            if book is not None:
                return book
        return self._order_book_via_rest(token_id)

    def _order_book_via_sdk(self, clob, token_id: str) -> OrderBook | None:
        try:
            summary = clob.get_order_book(token_id)
        except Exception:
            return None
        bids = _parse_levels(getattr(summary, "bids", None), descending=True)
        asks = _parse_levels(getattr(summary, "asks", None), descending=False)
        if not bids and not asks:
            return None
        return OrderBook(token_id=token_id, bids=bids, asks=asks)

    def _order_book_via_rest(self, token_id: str) -> OrderBook | None:
        try:
            r = self._http.get(f"{self.clob_base}/book", params={"token_id": token_id})
            r.raise_for_status()
            payload = r.json() or {}
        except Exception:
            return None
        bids = _parse_levels(payload.get("bids"), descending=True)
        asks = _parse_levels(payload.get("asks"), descending=False)
        if not bids and not asks:
            return None
        return OrderBook(token_id=token_id, bids=bids, asks=asks)


def _level_price_size(lvl: Any) -> tuple[float, float] | None:
    """Read (price, size) from either a dict (REST) or an OrderSummary (SDK)."""
    try:
        if isinstance(lvl, dict):
            return float(lvl["price"]), float(lvl["size"])
        return float(lvl.price), float(lvl.size)   # py-clob-client OrderSummary
    except (KeyError, AttributeError, ValueError, TypeError):
        return None


def _parse_levels(raw: object, descending: bool) -> list[OrderBookLevel]:
    """Normalise book levels (dicts or SDK objects) and sort best-first.

    Polymarket returns levels worst-first; we sort so index 0 is the best price
    on each side (highest bid / lowest ask).
    """
    levels: list[OrderBookLevel] = []
    if isinstance(raw, list):
        for lvl in raw:
            ps = _level_price_size(lvl)
            if ps is not None:
                levels.append(OrderBookLevel(price=ps[0], size=ps[1]))
    levels.sort(key=lambda l: l.price, reverse=descending)
    return levels
