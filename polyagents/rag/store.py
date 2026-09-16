"""ChromaDB RAG over Polymarket markets **and** news — the retrieval layer.

Vectorises two evidence sources into local Chroma collections (free all-MiniLM
embeddings, no API key):

  * ``polyagents_markets`` — one vector per market (question + description), so the
    signal agent can retrieve semantically *similar past markets* as context.
  * ``polyagents_news``    — news items **split into overlapping chunks** (title +
    snippet), so an event/headline can be matched to the most relevant evidence
    passage. Chunk hits are de-duplicated back to their parent item at recall.

Graceful: if ``chromadb`` isn't installed or RAG is disabled, ``enabled`` is False
and index/query become no-ops returning ``[]`` (with a keyword fallback).
"""
from __future__ import annotations

import re
from typing import Any

from polyagents.dataflows.types import Market

_MARKETS = "polyagents_markets"
_NEWS = "polyagents_news"

# Chunking: news snippets are longer than a market question, so split into
# overlapping windows — improves recall precision (a hit points at the passage,
# not the whole blob) while overlap avoids cutting an entity across a boundary.
_CHUNK_CHARS = 400
_CHUNK_OVERLAP = 80


def _chunk(text: str, size: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping character windows (sentence-aware at the edges)."""
    text = " ".join((text or "").split())
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):                                 # try to break on a sentence/space boundary
            cut = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


class ChromaRAG:
    def __init__(self, path: str | None = None, collection: str = _MARKETS,
                 client: Any | None = None) -> None:
        self.path = path
        self._names = {"market": collection, "news": _NEWS}
        self._cols: dict[str, Any] = {}
        self._client = client          # injectable for tests
        self._disabled = False
        self._fallback: dict[str, dict] = {}               # {kind: {id: {...}}}

    # ----- lifecycle ---------------------------------------------------------

    def _collection(self, kind: str = "market"):
        if kind in self._cols or self._disabled:
            return self._cols.get(kind)
        try:
            if self._client is None:
                import chromadb
                self._client = (
                    chromadb.PersistentClient(path=self.path) if self.path
                    else chromadb.EphemeralClient()
                )
            self._cols[kind] = self._client.get_or_create_collection(self._names[kind])
        except Exception:
            self._disabled = True
            return None
        return self._cols.get(kind)

    @property
    def enabled(self) -> bool:
        return self._collection("market") is not None

    # ----- writes: markets ---------------------------------------------------

    def index_market(self, market: Market) -> None:
        col = self._collection("market")
        doc = f"{market.question}\n{market.description}".strip()
        metadata = {
            "question": market.question, "condition_id": market.condition_id,
            "outcome": market.outcome, "price": float(market.price),
        }
        mid = market.condition_id or market.market_id
        if col is None:                                     # chroma disabled → no-op
            return
        try:
            col.upsert(ids=[mid], documents=[doc], metadatas=[metadata])
        except Exception:                                   # collection exists but write failed → keyword fallback
            self._fallback.setdefault("market", {})[mid] = {"document": doc, "metadata": metadata}

    def annotate_outcome(self, condition_id: str, winner: str) -> None:
        """Tag a market's vector with its resolved winner (closes the RAG loop)."""
        col = self._collection("market")
        if not condition_id:
            return
        fb = self._fallback.get("market", {})
        if condition_id in fb:
            fb[condition_id]["metadata"]["resolved_winner"] = winner
        if col is not None:
            try:
                col.update(ids=[condition_id], metadatas=[{"resolved_winner": winner}])
            except Exception:
                pass

    # ----- writes: news (chunked) --------------------------------------------

    def index_news(self, items: list, *, topic: str = "") -> int:
        """Chunk news items (title + snippet) and upsert each chunk as its own vector,
        keyed ``<parent>#<i>`` with a ``parent_id`` so recall can de-dup to the item."""
        col = self._collection("news")
        if col is None:                                     # chroma disabled → no-op
            return 0
        n = 0
        for it in items or []:
            title = str(getattr(it, "title", "") or (it.get("title") if isinstance(it, dict) else ""))
            snippet = str(getattr(it, "snippet", "") or (it.get("snippet") if isinstance(it, dict) else ""))
            url = str(getattr(it, "url", "") or (it.get("url") if isinstance(it, dict) else ""))
            sentiment = float(it.get("sentiment", 0.0)) if isinstance(it, dict) else 0.0
            parent = url or f"news_{abs(hash(title)) % 10**10}"
            for i, ch in enumerate(_chunk(f"{title}. {snippet}")):
                cid = f"{parent}#{i}"
                meta = {"kind": "news", "title": title[:180], "url": url,
                        "topic": topic[:120], "sentiment": sentiment,
                        "parent_id": parent, "chunk_i": i}
                try:
                    col.upsert(ids=[cid], documents=[ch], metadatas=[meta])
                except Exception:                           # write failed → keyword fallback
                    self._fallback.setdefault("news", {})[cid] = {"document": ch, "metadata": meta}
                n += 1
        return n

    # ----- reads -------------------------------------------------------------

    def query_similar(self, text: str, n: int = 3, exclude_id: str | None = None) -> list[dict]:
        """Semantically similar past MARKETS (context for the signal agent)."""
        return self._query("market", text, n=n, exclude_id=exclude_id)

    def query_news(self, text: str, n: int = 3) -> list[dict]:
        """Most relevant NEWS items for an event/headline — chunk hits de-duped to
        their parent item (so one article can't flood the results)."""
        hits = self._query("news", text, n=n * 3)          # over-fetch chunks, then de-dup
        seen, out = set(), []
        for h in hits:
            pid = (h.get("metadata") or {}).get("parent_id") or h["id"]
            if pid in seen:
                continue
            seen.add(pid)
            out.append(h)
            if len(out) >= n:
                break
        return out

    def _query(self, kind: str, text: str, n: int = 3, exclude_id: str | None = None) -> list[dict]:
        col = self._collection(kind)
        if not text:
            return []
        if col is None or self._fallback.get(kind):
            return self._query_fallback(kind, text, n=n, exclude_id=exclude_id)
        try:
            res = col.query(query_texts=[text], n_results=n + (1 if exclude_id else 0))
        except Exception:
            return self._query_fallback(kind, text, n=n, exclude_id=exclude_id)
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

    def count(self, kind: str = "market") -> int:
        fb = self._fallback.get(kind)
        if fb:
            return len(fb)
        col = self._collection(kind)
        try:
            return col.count() if col is not None else 0
        except Exception:
            return 0

    def _query_fallback(self, kind: str, text: str, n: int = 3, exclude_id: str | None = None) -> list[dict]:
        query = _tokens(text)
        scored = []
        for item_id, item in (self._fallback.get(kind) or {}).items():
            if exclude_id and item_id == exclude_id:
                continue
            overlap = len(query & _tokens(item["document"]))
            if overlap:
                scored.append((overlap, item_id, item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [{"id": iid, "document": it["document"], "metadata": dict(it["metadata"])}
                for _, iid, it in scored[:n]]


def _tokens(text: str) -> set[str]:
    tokens = {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}
    for a, b in (("btc", "bitcoin"), ("eth", "ethereum")):
        if a in tokens:
            tokens.add(b)
        if b in tokens:
            tokens.add(a)
    return tokens
