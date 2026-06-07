"""Read-only Polymarket data client.

Wraps three public Polymarket surfaces over plain ``httpx`` — no API keys, no
SDK, no signing — because the data-collection layer only reads:

  * Gamma REST       — active-market metadata (discovery)
  * CLOB REST        — ``/prices-history`` and the public ``/book`` order book
  * data-api REST    — ``/trades`` (taker/maker fills, for volume + flow)

Trading (order placement, which *does* need keys) belongs to the later
execution layer and is deliberately not part of this client. The logic mirrors
polymarket's ``src/data/polymarket_client.py``; the order book is fetched from
the public REST endpoint instead of the authenticated SDK so the whole layer
runs credential-free.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

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
    ) -> None:
        self.gamma_base = gamma_base.rstrip("/")
        self.clob_base = clob_base.rstrip("/")
        self.data_api_base = data_api_base.rstrip("/")
        self._http = http or httpx.Client(timeout=timeout)

    @classmethod
    def from_config(cls, config: dict, http: httpx.Client | None = None) -> "PolymarketDataClient":
        return cls(
            gamma_base=config["gamma_base"],
            clob_base=config["clob_base"],
            data_api_base=config["data_api_base"],
            timeout=config.get("http_timeout", 20.0),
            http=http,
        )

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
        """Synthetic candles (open=high=low=close=price); volume filled later."""
        try:
            r = self._http.get(
                f"{self.clob_base}/prices-history",
                params={"market": token_id, "interval": interval, "fidelity": fidelity},
            )
            r.raise_for_status()
            history = r.json().get("history", [])
        except Exception:
            return []
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

    # ----- order book (public CLOB REST) ------------------------------------

    def fetch_order_book(self, token_id: str) -> OrderBook | None:
        """Public ``/book`` snapshot — no auth required, unlike the SDK path."""
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


def _parse_levels(raw: object, descending: bool) -> list[OrderBookLevel]:
    """Normalise ``[{"price","size"}, ...]`` and sort by price.

    Polymarket returns book levels worst-first; we sort so index 0 is the best
    price on each side (highest bid / lowest ask).
    """
    levels: list[OrderBookLevel] = []
    if isinstance(raw, list):
        for lvl in raw:
            try:
                levels.append(OrderBookLevel(price=float(lvl["price"]), size=float(lvl["size"])))
            except (KeyError, ValueError, TypeError):
                continue
    levels.sort(key=lambda l: l.price, reverse=descending)
    return levels
