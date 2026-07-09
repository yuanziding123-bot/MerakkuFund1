"""Tavily news search, with a graceful no-key fallback.

Adapted from polymarket's ``src/data/news_client.py``. When ``TAVILY_API_KEY``
is unset (or the package is missing) ``search`` returns ``[]`` and the news
collector degrades to a "no news available" report rather than failing the run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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

    @staticmethod
    def _items_from_response(resp: dict) -> list[NewsItem]:
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

    def search(self, query: str, max_results: int = 5) -> list[NewsItem]:
        if not self._tavily:
            return []
        try:
            resp = self._tavily.search(query=query, max_results=max_results, search_depth="basic")
        except Exception:
            return []
        return self._items_from_response(resp)

    def search_between(
        self,
        query: str,
        *,
        start: datetime,
        end: datetime,
        max_results: int = 5,
    ) -> list[NewsItem]:
        """Search news in a date window.

        Tavily accepts date strings, but callers still need to validate each
        returned item against point-in-time constraints because search APIs can
        return undated or loosely dated results.
        """
        if not self._tavily:
            return []
        try:
            resp = self._tavily.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
                topic="news",
                start_date=start.date().isoformat(),
                end_date=end.date().isoformat(),
            )
        except Exception:
            return []
        return self._items_from_response(resp)
