"""SQLite data store for the Layer 1 data layer.

Persists what the collectors fetch so repeated runs don't re-hit the API and so
history accumulates for later ML / backtesting. Stdlib ``sqlite3`` only — the
same lightweight approach as the polymarket reference repo's TraceStore.

Tables:
  * ``markets``             — market metadata snapshots (one row per fetch)
  * ``candles``             — price-history bars per token (upsert by ts)
  * ``trades``              — raw trades per condition (deduped); powers the
                              volume-reconstruction cache and is the big API saver
  * ``orderbook_snapshots`` — L2 microstructure over time
  * ``collections``         — the full ``raw`` factor bundle per collect() run
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from polyagents.dataflows.types import Candle, Market

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    token_id TEXT, condition_id TEXT, market_id TEXT, question TEXT, outcome TEXT,
    price REAL, volume_24h REAL, liquidity REAL, spread REAL, days_to_expiry REAL,
    expiry TEXT, fetched_at TEXT,
    PRIMARY KEY (token_id, fetched_at)
);
CREATE TABLE IF NOT EXISTS candles (
    token_id TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL,
    fetched_at TEXT,
    PRIMARY KEY (token_id, ts)
);
CREATE TABLE IF NOT EXISTS trades (
    trade_key TEXT PRIMARY KEY,
    condition_id TEXT, asset TEXT, timestamp INTEGER, size REAL, price REAL, side TEXT
);
CREATE INDEX IF NOT EXISTS trades_cond_ts ON trades (condition_id, timestamp);
CREATE TABLE IF NOT EXISTS trade_coverage (
    condition_id TEXT PRIMARY KEY, min_fetched INTEGER
);
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    token_id TEXT, ts TEXT, best_bid REAL, best_ask REAL, mid REAL, micro_price REAL,
    spread_bps REAL, book_pressure REAL, data TEXT,
    PRIMARY KEY (token_id, ts)
);
CREATE TABLE IF NOT EXISTS collections (
    token_id TEXT, as_of TEXT, question TEXT, market_price REAL, raw TEXT,
    PRIMARY KEY (token_id, as_of)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trade_key(t: dict) -> str:
    base = t.get("transactionHash") or t.get("id") or ""
    raw = f"{base}|{t.get('asset')}|{t.get('timestamp')}|{t.get('size')}|{t.get('price')}|{t.get('side')}"
    return hashlib.sha1(raw.encode()).hexdigest()


class DataStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ----- markets -----------------------------------------------------------

    def record_market(self, m: Market, fetched_at: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (m.token_id, m.condition_id, m.market_id, m.question, m.outcome, m.price,
             m.volume_24h, m.liquidity, m.spread, m.days_to_expiry,
             m.expiry.isoformat() if m.expiry else None, fetched_at or _now()),
        )
        self.conn.commit()

    # ----- candles -----------------------------------------------------------

    def upsert_candles(self, token_id: str, candles: Iterable[Candle]) -> int:
        fetched = _now()
        rows = [
            (token_id, int(c.ts.timestamp()), c.open, c.high, c.low, c.close, c.volume, fetched)
            for c in candles
        ]
        self.conn.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def get_candles(self, token_id: str) -> list[Candle]:
        cur = self.conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles WHERE token_id=? ORDER BY ts",
            (token_id,),
        )
        return [
            Candle(datetime.fromtimestamp(r["ts"], tz=timezone.utc), r["open"], r["high"],
                   r["low"], r["close"], r["volume"])
            for r in cur.fetchall()
        ]

    # ----- trades (cache for volume reconstruction) --------------------------

    def insert_trades(self, condition_id: str, raw_trades: Iterable[dict]) -> int:
        rows = []
        for t in raw_trades:
            ts, size = t.get("timestamp"), t.get("size")
            if ts is None or size is None:
                continue
            try:
                rows.append((_trade_key(t), condition_id, str(t.get("asset") or ""),
                             int(ts), float(size), float(t.get("price") or 0.0), str(t.get("side") or "")))
            except (TypeError, ValueError):
                continue
        self.conn.executemany("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def trade_coverage(self, condition_id: str) -> int | None:
        """Oldest timestamp we've fetched /trades down to (a fetch watermark).

        Tracked separately from the oldest *trade* so a market with no early
        trades isn't re-fetched every run.
        """
        r = self.conn.execute(
            "SELECT min_fetched FROM trade_coverage WHERE condition_id=?", (condition_id,)
        ).fetchone()
        return r["min_fetched"] if r else None

    def mark_trade_coverage(self, condition_id: str, min_ts: int) -> None:
        existing = self.trade_coverage(condition_id)
        new = min(existing, min_ts) if existing is not None else min_ts
        self.conn.execute(
            "INSERT OR REPLACE INTO trade_coverage VALUES (?,?)", (condition_id, new)
        )
        self.conn.commit()

    def trade_ts_range(self, condition_id: str) -> tuple[int, int] | None:
        cur = self.conn.execute(
            "SELECT MIN(timestamp) lo, MAX(timestamp) hi FROM trades WHERE condition_id=?",
            (condition_id,),
        )
        r = cur.fetchone()
        return (r["lo"], r["hi"]) if r and r["lo"] is not None else None

    def fetch_trades(self, condition_id: str, min_ts: int | None = None,
                     max_ts: int | None = None, asset: str | None = None) -> list[dict]:
        q = "SELECT asset, timestamp, size, price, side FROM trades WHERE condition_id=?"
        args: list[Any] = [condition_id]
        if asset is not None:
            q += " AND asset=?"; args.append(asset)
        if min_ts is not None:
            q += " AND timestamp>=?"; args.append(min_ts)
        if max_ts is not None:
            q += " AND timestamp<=?"; args.append(max_ts)
        q += " ORDER BY timestamp"
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    # ----- order book snapshots ---------------------------------------------

    def record_orderbook(self, token_id: str, data: dict, ts: str | None = None) -> None:
        ts = ts or _now()
        self.conn.execute(
            "INSERT OR REPLACE INTO orderbook_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
            (token_id, ts, data.get("best_bid"), data.get("best_ask"), data.get("mid"),
             data.get("micro_price"), data.get("spread_bps"), data.get("book_pressure"),
             json.dumps(data, ensure_ascii=False)),
        )
        self.conn.commit()

    # ----- collections (full raw bundle per run) -----------------------------

    def record_collection(self, token_id: str, as_of: str, question: str,
                          market_price: float, raw: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO collections VALUES (?,?,?,?,?)",
            (token_id, as_of, question, market_price, json.dumps(raw, ensure_ascii=False)),
        )
        self.conn.commit()

    def collection_exists(self, token_id: str, as_of: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM collections WHERE token_id=? AND as_of=?",
            (token_id, as_of),
        ).fetchone()
        return row is not None

    def fetch_collections(self, min_as_of: str | None = None, max_as_of: str | None = None,
                          limit: int = 500) -> list[dict]:
        """Return stored collection runs for research/backtest consumers."""
        q = "SELECT token_id, as_of, question, market_price, raw FROM collections WHERE 1=1"
        args: list[Any] = []
        if min_as_of is not None:
            q += " AND as_of>=?"
            args.append(min_as_of)
        if max_as_of is not None:
            q += " AND as_of<=?"
            args.append(max_as_of)
        q += " ORDER BY as_of LIMIT ?"
        args.append(limit)
        rows = []
        for r in self.conn.execute(q, args).fetchall():
            rows.append({
                "token_id": r["token_id"],
                "as_of": r["as_of"],
                "question": r["question"],
                "market_price": r["market_price"],
                "raw": json.loads(r["raw"] or "{}"),
            })
        return rows

    # ----- introspection -----------------------------------------------------

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for tbl in ("markets", "candles", "trades", "orderbook_snapshots", "collections"):
            out[tbl] = self.conn.execute(f"SELECT COUNT(*) c FROM {tbl}").fetchone()["c"]
        return out
