"""Kernel capability tiers — a small always-on CORE plus selectable vertical PACKS.

Per the mentor's model: the agent has a built-in core, and the many *vertical*
capabilities load on demand — that on-demand load *is* the selection. So the kernel
registry = CORE (always) + the capabilities of whatever vertical packs the user (or a
router) selected for this session. Adding a vertical = a new pack, not more always-on
capabilities, so the controller's menu stays tight as verticals grow.
"""
from __future__ import annotations

#: Always loaded — everyday research / analysis / recommendation / Q&A / opportunity hunt.
CORE: list[str] = [
    "hunt_alpha",                                       # top-level opportunity scan
    "scan_markets", "resolve_market", "analyze_market",
    "discover_markets", "recommend_markets",
    "evaluate_skill", "portfolio_review",              # do we have skill? / paper P&L
    "langgraph_answer", "domain_answer",
]

#: Selectable vertical packs: id -> {name, description, capabilities}.
PACKS: dict[str, dict] = {
    "backtest-lab": {
        "name": "回测 & 策略实验室",
        "description": "批量采集、单/多策略回测对比、Lab 晋级门(paper-ready 判定)。",
        "capabilities": ["batch_collect", "batch_backtest", "backtest_strategies",
                         "promotion_gate", "data_agent", "backtest_agent"],
    },
    "crypto-arb": {
        "name": "跨市场 crypto 套利",
        "description": "用交易所现货 + 波动率找 Polymarket crypto 市场的错价机会。",
        "capabilities": ["find_crypto_arb"],
    },
    "microstructure": {
        "name": "微结构 / 资金流扫描",
        "description": "跨市场扫订单簿微结构 + 交易流,找'资金领先、价格滞后'的潜在 edge。",
        "capabilities": ["microstructure_scan"],
    },
    "news-events": {
        "name": "新闻 / 事件情绪",
        "description": "拉某市场/主题的新闻并打情绪分,事件驱动信号(需 TAVILY_API_KEY)。",
        "capabilities": ["news_sentiment"],
    },
    "strategy-supervisor": {
        "name": "多智能体策略",
        "description": "data→signal→risk 监督者一条龙,给一个市场出决策。",
        "capabilities": ["strategy"],
    },
    "paper-exec": {
        "name": "纸面交易(动手)",
        "description": "对市场 size+过风控+下纸面单,以及结算+反思学习(paper money)。gated:选中才能让 loop 动手。",
        "capabilities": ["paper_trade", "settle_and_reflect"],
    },
}


def pack_capabilities(selected: list[str] | None) -> list[str]:
    """Capability names for the selected packs. ``None`` = load every pack (default,
    backward-compatible); ``[]`` = core only; a list = just those packs."""
    ids = list(PACKS) if selected is None else selected
    names: list[str] = []
    for pid in ids:
        names += PACKS.get(pid, {}).get("capabilities", [])
    return names


def kernel_capability_names(selected: list[str] | None) -> list[str]:
    """The ordered, de-duplicated capability names for a kernel session: CORE + packs."""
    return list(dict.fromkeys(CORE + pack_capabilities(selected)))
