"""ChromaDB RAG over Polymarket markets — the Polymarket/agents capability.

The official Polymarket/agents framework (archived, OpenAI-based, Python 3.9)
overlaps everything polyagents already does *except* one thing the doc credits
it for: **RAG with Chroma** (vectorising market/news data for retrieval). We
adopt that natively here with `chromadb` (free, local all-MiniLM embeddings — no
API key), indexing the markets we collect so the signal agent can retrieve
semantically *similar* past markets as context.

Graceful: if `chromadb` isn't installed or RAG is disabled, `enabled` is False
and index/query become no-ops returning ``[]``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from polyagents.dataflows.types import Market

_COLLECTION = "polyagents_markets"


class ChromaRAG:
    def __init__(self, path: str | None = None, collection: str = _COLLECTION,
                 client: Any | None = None) -> None:
        self.path = path
        self._collection_name = collection
        self._col = None
        self._client = client          # injectable for tests
        self._disabled = False
        self._fallback: dict[str, dict] = {}

    # ----- lifecycle ---------------------------------------------------------

    def _collection(self):
        if self._col is not None or self._disabled:
            return self._col
        try:
            if self._client is None:
                import chromadb
                self._client = (
                    chromadb.PersistentClient(path=self.path) if self.path
                    else chromadb.EphemeralClient()
                )
            self._col = self._client.get_or_create_collection(self._collection_name)
        except Exception:
            self._disabled = True
            self._col = None
        return self._col

    @property
    def enabled(self) -> bool:
        return self._collection() is not None

    # ----- writes ------------------------------------------------------------

    def index_market(self, market: Market) -> None:
        col = self._collection()
        if col is None:
            return
        doc = f"{market.question}\n{market.description}".strip()
        metadata = {
            "question": market.question,
            "condition_id": market.condition_id,
            "outcome": market.outcome,
            "price": float(market.price),
        }
        try:
            col.upsert(
                ids=[market.condition_id or market.market_id],
                documents=[doc],
                metadatas=[metadata],
            )
        except Exception:
            self._fallback[market.condition_id or market.market_id] = {
                "document": doc,
                "metadata": metadata,
            }

    def annotate_outcome(self, condition_id: str, winner: str) -> None:
        """Tag a market's vector with its resolved winner (closes the RAG loop)."""
        col = self._collection()
        if col is None or not condition_id:
            return
        if condition_id in self._fallback:
            self._fallback[condition_id]["metadata"]["resolved_winner"] = winner
        try:
            col.update(ids=[condition_id], metadatas=[{"resolved_winner": winner}])
        except Exception:
            pass

    # ----- reads -------------------------------------------------------------

    def query_similar(self, text: str, n: int = 3, exclude_id: str | None = None) -> list[dict]:
        col = self._collection()
        if col is None or not text:
            return []
        if self._fallback:
            return self._query_fallback(text, n=n, exclude_id=exclude_id)
        try:
            res = col.query(query_texts=[text], n_results=n + (1 if exclude_id else 0))
        except Exception:
            return self._query_fallback(text, n=n, exclude_id=exclude_id)
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        out: list[dict] = []
        for i, d, m in zip(ids, docs, metas):
            if exclude_id and i == exclude_id:
                continue
            out.append({"id": i, "document": d, "metadata": m or {}})
            if len(out) >= n:
                break
        return out

    def count(self) -> int:
        col = self._collection()
        if self._fallback:
            return len(self._fallback)
        try:
            return col.count() if col is not None else 0
        except Exception:
            return 0

    def _query_fallback(self, text: str, n: int = 3, exclude_id: str | None = None) -> list[dict]:
        query = _tokens(text)
        scored = []
        for item_id, item in self._fallback.items():
            if exclude_id and item_id == exclude_id:
                continue
            doc_tokens = _tokens(item["document"])
            overlap = len(query & doc_tokens)
            if overlap:
                scored.append((overlap, item_id, item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [
            {"id": item_id, "document": item["document"], "metadata": dict(item["metadata"])}
            for _, item_id, item in scored[:n]
        ]


def _tokens(text: str) -> set[str]:
    tokens = {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}
    if "btc" in tokens:
        tokens.add("bitcoin")
    if "bitcoin" in tokens:
        tokens.add("btc")
    if "eth" in tokens:
        tokens.add("ethereum")
    if "ethereum" in tokens:
        tokens.add("eth")
    return tokens
