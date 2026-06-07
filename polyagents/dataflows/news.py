"""Tavily news search, with a graceful no-key fallback.

Adapted from polymarket's ``src/data/news_client.py``. When ``TAVILY_API_KEY``
is unset (or the package is missing) ``search`` returns ``[]`` and the news
collector degrades to a "no news available" report rather than failing the run.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NewsItem:
    title: str
    url: str
    snippet: str
    published: str | None = None


class NewsClient:
    def __init__(self, api_key: str | None) -> None:
        self._tavily = None
        if api_key:
            try:
                from tavily import TavilyClient

                self._tavily = TavilyClient(api_key=api_key)
            except ImportError:
                self._tavily = None

    @property
    def enabled(self) -> bool:
        return self._tavily is not None

    def search(self, query: str, max_results: int = 5) -> list[NewsItem]:
        if not self._tavily:
            return []
        try:
            resp = self._tavily.search(query=query, max_results=max_results, search_depth="basic")
        except Exception:
            return []
        out: list[NewsItem] = []
        for r in resp.get("results", []):
            out.append(
                NewsItem(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=(r.get("content", "") or "")[:500],
                    published=r.get("published_date"),
                )
            )
        return out
