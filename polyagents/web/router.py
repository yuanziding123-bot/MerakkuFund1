"""Question router for Ask — pick an answer mode before building the agent.

Ask questions vary a lot: deterministic domain questions (our markets / data /
evaluation) should use the LangGraph tool agent; open-ended / coding / external
questions should go to a general agent. Classification is cheap-first:

  1. an explicit manual choice from the composer wins,
  2. then high-precision keyword rules (zero tokens),
  3. then a cheap Haiku fallback for the genuinely ambiguous,

defaulting to ``domain`` (this app's primary job is market research) when nothing
else decides. Returns ``(route, by)`` so the decision is auditable.
"""
from __future__ import annotations

DOMAIN_KW = (
    "market", "markets", "polymarket", "kalshi", "odds", "probability", "brier",
    "calibrat", "evaluate", "evaluation", "edge", "hypothesis", "backtest",
    "orderbook", "order book", "liquidity", "spread", "scan", "p_true", "p_cal",
    "resolve", "settle", "portfolio", "alpha", "promote",
    "市场", "概率", "校准", "评估", "假设", "回测", "订单簿", "流动性", "点差",
    "成交", "行情", "跑赢", "仓位", "结算",
)
GENERAL_KW = (
    "write code", "coding", "code", "script", "program", "function", "regex",
    "explain", "what is", "what's", "how do", "how to", "translate", "summarize",
    "summary", "difference between", "industry", "essay", "rewrite",
    "写代码", "脚本", "代码", "函数", "解释", "什么是", "怎么", "如何", "翻译",
    "总结", "对比", "区别", "介绍", "改写",
)

_CLASSIFY_SYS = (
    "Classify a user question for a prediction-market research assistant. "
    "Reply with ONE word only:\n"
    "- domain = about prediction markets, our data, probabilities, evaluation, "
    "hypotheses, or trading research.\n"
    "- general = open-ended, coding, concept explanations, translation, or "
    "information outside the user's prediction markets."
)


def _llm_classify(message: str, llm) -> str:
    try:
        resp = llm.invoke([("system", _CLASSIFY_SYS), ("user", message)])
        text = getattr(resp, "content", resp)
        if isinstance(text, list):
            text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
        t = str(text).lower()
        if "general" in t and "domain" not in t:
            return "general"
        return "domain"
    except Exception:
        return "domain"


def classify(message: str, *, manual: str | None = None, llm=None) -> tuple[str, str]:
    """Return ``(route, by)`` — route in {'domain','general'}, by in
    {'manual','rule','llm','default'}."""
    if manual in ("domain", "general"):
        return manual, "manual"
    text = (message or "").lower()
    if any(k in text for k in DOMAIN_KW):       # domain wins on overlap (it's the specialty)
        return "domain", "rule"
    if any(k in text for k in GENERAL_KW):
        return "general", "rule"
    if llm is not None:
        return _llm_classify(message, llm), "llm"
    return "domain", "default"
