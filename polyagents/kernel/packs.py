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
    "hunt_alpha",                                       # top-level opportunity scan (edge detectors)
    "scan_opportunities",                              # Lab monitor: strategy-scored actionable trades
    "scan_markets", "resolve_market", "analyze_market",
    "discover_markets", "recommend_markets",
    "plot_market",                                     # visualize as an inline SVG chart
    "evaluate_skill", "portfolio_review",              # do we have skill? / paper P&L
    "langgraph_answer", "domain_answer",
]

#: Selectable vertical packs: id -> {name, description, capabilities}.
PACKS: dict[str, dict] = {
    "backtest-lab": {
        "name": "回测 & 策略实验室",
        "description": "批量采集、单/多策略回测对比、策略×领域矩阵、Lab 晋级门(paper-ready 判定)。",
        "capabilities": ["batch_collect", "batch_backtest", "backtest_strategies",
                         "backtest_matrix", "promotion_gate"],
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
    "market-radar": {
        "name": "市场雷达(今天有什么变了)",
        "description": "扫全市场,surface 给人肉深挖的线索:近期价格**异动**最大的、**临近结算**的(endgame)、"
                       "**短历史/可能新上市**的。只给候选、不下结论——主观找 alpha 的发现漏斗。",
        "capabilities": ["market_radar"],
    },
    "conditional-arb": {
        "name": "跨市场条件套利扫描",
        "description": "扫全市场找'冠军 × 晋级/单场'的条件概率关联链:算 P(夺冠|晋级)=P(夺冠)/P(晋级),"
                       "标出**真·逻辑蕴含套利**(强命题反而更贵=无风险),并把它与'条件概率方向性价值'分开。"
                       "注:P(单场)×P(夺冠) 那种链式成本不是有效套利,本 skill 只报真的。",
        "capabilities": ["scan_conditional_arb"],
    },
    "alpha-research": {
        "name": "关联 alpha 研究(策略验证 + 改进)",
        "description": "针对一个标的验证你的策略/假设有没有 alpha,并给改进意见。核心是事件关联性:"
                       "互斥冠军集一致性 + 再分配 + 滞后检测(别的场次一动、这场没跟上=机会)+ what-if "
                       "敏感度,再叠加新闻情绪,LLM 据数给判定与改进。",
        "capabilities": ["relational_alpha", "research_alpha"],
    },
    "lab-backtest": {
        "name": "Lab 回测(特征策略 + 结果回填)",
        "description": "先给已采集的市场快照回填结算结果(写入共享库,设了 POLYAGENTS_DATABASE_URL "
                       "即云端 postgres),再用 Lab 的 7 个特征策略在带标签快照上跑完整回测"
                       "(Brier vs 市场 + 校准 + 晋级门)。选中后 Ask 里可直接调用 Lab 回测。",
        "capabilities": ["backfill_outcomes", "lab_backtest"],
    },
    # NOTE: the old "strategy-supervisor" pack (data→signal→risk supervisor) was pruned —
    # analyze_market fully supersedes it (signal + sized decision + backtest + reflection).
    # The strategy capability stays defined for the non-kernel "strategy" mode.
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
