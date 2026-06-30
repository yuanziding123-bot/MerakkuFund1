"""Intent recognition — turn a natural request into a Goal (target effects).

Rule-based for P1 (an LLM recogniser is P2). The Goal's ``targets`` are what the
planner reasons backward from, so "…做 backtest" yields ``{backtest_report}`` and
the loop derives data → backtest on its own.
"""
from __future__ import annotations

from .core import Goal

_BACKTEST = ("backtest", "回测", "back-test")
_TRADE = ("size a position", "size position", "下单", "仓位", "place a trade")
_EVAL = ("evaluate", "评估", "跑赢市场", "beat the market", "calibration report")


def recognize(request: str, *, event: str | None = None) -> Goal:
    t = (request or "").lower()
    if any(k in t for k in _BACKTEST):
        return Goal(frozenset({"backtest_report"}), {"event": event or request}, "backtest")
    if any(k in t for k in _TRADE):
        return Goal(frozenset({"decision"}), {"event": event or request}, "trade")
    if any(k in t for k in _EVAL):
        return Goal(frozenset({"evaluation"}), {}, "evaluate")
    return Goal(frozenset({"answer"}), {"question": request}, "ask")
