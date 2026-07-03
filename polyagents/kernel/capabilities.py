"""Finance capabilities for the kernel — declared with preconditions/effects so
the planner can chain them automatically.

Each builder takes its worker by injection (``fetch_fn`` etc.), so the registry
runs offline in tests and can be wired to the real engine in prod. The capability
set passed to an :class:`~polyagents.kernel.core.AgentLoop` *is* the "mode".
"""
from __future__ import annotations

from typing import Callable

from .core import Capability, Context


def data_capability(fetch_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"history": fetch_fn(ctx.facts.get("event"))}
    return Capability("data_agent", "Fetch historical data for an event/market.",
                      frozenset({"event"}), frozenset({"history"}), run, cost=1)


def backtest_capability(backtest_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"backtest_report": backtest_fn(ctx.facts["history"])}
    return Capability("backtest_agent", "Backtest a signal over historical data → report.",
                      frozenset({"history"}), frozenset({"backtest_report"}), run, cost=2)


def signal_capability(signal_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"signal": signal_fn(ctx.facts["history"])}
    return Capability("signal_agent", "Estimate a probability signal from data.",
                      frozenset({"history"}), frozenset({"signal"}), run, cost=2)


def risk_capability(risk_fn: Callable) -> Capability:
    def run(ctx: Context) -> dict:
        return {"decision": risk_fn(ctx.facts["signal"])}
    return Capability("risk_agent", "Size + risk-gate a decision from a signal.",
                      frozenset({"signal"}), frozenset({"decision"}), run, cost=2)


def _answer_stream(stream_fn: Callable | None):
    """Build the optional ``stream(ctx, emit)`` for an answer capability, if a
    streaming worker ``stream_fn(question, emit) -> str`` was supplied."""
    if stream_fn is None:
        return None

    def stream(ctx: Context, emit: Callable[[dict], None]) -> dict:
        return {"answer": stream_fn(ctx.facts.get("question", ""), emit)}
    return stream


def answer_capability(answer_fn: Callable, *, stream_fn: Callable | None = None) -> Capability:
    """Wrap the existing LangGraph ReAct agent as ONE capability: question → answer.

    This is the point of the kernel — LangGraph becomes a capability inside the
    loop, not the top-level orchestrator. ``answer_fn(question) -> str`` (blocking);
    optional ``stream_fn(question, emit) -> str`` streams inner tokens via ``emit``.
    """
    def run(ctx: Context) -> dict:
        return {"answer": answer_fn(ctx.facts.get("question", ""))}
    return Capability("langgraph_answer",
                      "General / open-ended Q&A (concepts, coding, outside info) via a "
                      "general agent with web search. NOT for our own market data.",
                      frozenset({"question"}), frozenset({"answer"}), run, cost=3,
                      stream=_answer_stream(stream_fn))


def domain_capability(answer_fn: Callable, *, stream_fn: Callable | None = None) -> Capability:
    """Wrap the read-only market-tools ReAct agent as ONE capability: question →
    answer, using live domain tools (scan / orderbook / evaluate). Same effect as
    ``langgraph_answer`` so the controller picks by *fit* — this one when the
    question is about OUR prediction markets / data / evaluation. ``answer_fn(q)->str``;
    optional ``stream_fn(q, emit) -> str`` streams inner tokens.
    """
    def run(ctx: Context) -> dict:
        return {"answer": answer_fn(ctx.facts.get("question", ""))}
    return Capability("domain_answer",
                      "ANSWER A QUESTION about OUR prediction markets / data / evaluation "
                      "(read-only look-ups: one market's orderbook, calibration/evaluate, "
                      "'what is the price of…'). Use ONLY to explain/answer — NOT to run a "
                      "batch job, collect/persist data, or backtest (use the batch_* / "
                      "scan_markets capabilities for those actions).",
                      frozenset({"question"}), frozenset({"answer"}), run, cost=3,
                      stream=_answer_stream(stream_fn))


def scan_capability(scan_fn: Callable) -> Capability:
    """Scan a BATCH of live markets — the first step of any batch data/backtest job.

    ``scan_fn(query) -> dict`` returns ``{"markets": [...], "count": n, ...}``. The
    batch lands on the blackboard as ``market_batch`` so ``batch_collect`` /
    ``batch_backtest`` can chain off it without re-scanning."""
    def run(ctx: Context) -> dict:
        query = ctx.facts.get("question") or ctx.facts.get("event")
        return {"market_batch": scan_fn(query)}
    return Capability("scan_markets",
                      "Scan/list a BATCH of live markets (most-active first, optionally "
                      "by the request's category). The first step for any 'batch run data', "
                      "batch collection, or batch backtest job.",
                      frozenset({"question"}), frozenset({"market_batch"}), run, cost=1)


def batch_collect_capability(collect_fn: Callable) -> Capability:
    """Batch-collect Layer-1 data for every market in the scanned batch → persist.

    ``collect_fn(market_batch) -> dict`` runs the L1 collector per market and writes
    through to the local store, returning how much was collected."""
    def run(ctx: Context) -> dict:
        return {"collections": collect_fn(ctx.facts["market_batch"])}
    return Capability("batch_collect",
                      "Batch-collect Layer-1 data (price / order-book microstructure / "
                      "trade-flow / factors) for EVERY market in the scanned batch and "
                      "persist it to the local store; returns how many markets / candles / "
                      "trades were collected. This is the action for 'batch run data'.",
                      frozenset({"market_batch"}), frozenset({"collections"}), run, cost=3)


def batch_backtest_capability(backtest_fn: Callable) -> Capability:
    """Backtest a signal across a whole batch of resolved markets → aggregate report.

    ``backtest_fn(query) -> dict`` slices resolved markets by the request and replays
    a deterministic signal, scoring vs the market baseline."""
    def run(ctx: Context) -> dict:
        query = ctx.facts.get("question") or ctx.facts.get("event")
        return {"backtest_report": backtest_fn(query)}
    return Capability("batch_backtest",
                      "Backtest a signal across a BATCH of resolved markets (sliced by the "
                      "request's category) → aggregate alpha / Brier report vs the market "
                      "baseline. Use for 'batch backtest' over many markets at once.",
                      frozenset({"question"}), frozenset({"backtest_report"}), run, cost=4)


def backtest_strategies_capability(fn: Callable) -> Capability:
    """Backtest SEVERAL strategy signals over a domain's resolved markets and compare.

    ``fn(query) -> dict`` runs each built-in signal (naive, momentum, …) over the
    domain's resolved markets and returns a comparison (brier_delta / beats_market
    per strategy) plus the best. Answers 'which strategy works in this domain'."""
    def run(ctx: Context) -> dict:
        return {"strategy_comparison": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("backtest_strategies",
                      "Backtest MULTIPLE strategy signals (naive, momentum, …) over a "
                      "domain's resolved markets and COMPARE them — which has alpha / beats "
                      "the market. Use for 'compare / backtest the strategies for <domain>'.",
                      frozenset({"question"}), frozenset({"strategy_comparison"}), run, cost=4)


def resolve_market_capability(resolve_fn: Callable) -> Capability:
    """Resolve the request to ONE concrete market to analyse.

    ``resolve_fn(question) -> dict`` returns ``{"token_id", "question", "price", ...}``
    (keyword-matched against live markets, else the most active). Lands as
    ``market_ref`` so ``analyze_market`` can run the framework on it."""
    def run(ctx: Context) -> dict:
        return {"market_ref": resolve_fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("resolve_market",
                      "Resolve the user's request to ONE concrete Polymarket market "
                      "(by token id, keyword match, or most-active). First step before "
                      "analyzing a specific market/target.",
                      frozenset({"question"}), frozenset({"market_ref"}), run, cost=1)


def analyze_market_capability(analyze_fn: Callable) -> Capability:
    """Goal-1 framework for a single target: explore → reason → analyze → backtest
    (historical comparison) → conclusion, as ONE loop capability.

    ``analyze_fn(market_ref) -> dict`` returns the structured ``market_analysis``
    (data reports + factors, the LLM p_true reasoning, a backtest of the signal over
    comparable resolved markets, similar-market precedents, and the sized/risk-gated
    conclusion). Also the base other trading instruments plug into."""
    def run(ctx: Context) -> dict:
        return {"market_analysis": analyze_fn(ctx.facts["market_ref"])}
    return Capability("analyze_market",
                      "Full analysis FRAMEWORK for one market/target: explore its data, "
                      "reason a true probability, analyze microstructure/flow, backtest the "
                      "signal over comparable resolved markets (historical comparison), and "
                      "give a sized, risk-gated conclusion. Use when the user wants to "
                      "analyze / evaluate a specific market or trading target.",
                      frozenset({"market_ref"}), frozenset({"market_analysis"}), run, cost=4)


def discover_markets_capability(discover_fn: Callable) -> Capability:
    """Goal-2 step 1: a theme / event / hot topic → candidate tradeable markets.

    ``discover_fn(topic) -> dict`` returns ``{"topic", "markets": [...], "count"}`` —
    active markets ranked by relevance to the topic (LLM-expanded keywords, so a
    Chinese topic still matches English market questions). Lands as ``candidates``."""
    def run(ctx: Context) -> dict:
        return {"candidates": discover_fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("discover_markets",
                      "Given a THEME / event / current hot topic (not a specific market), "
                      "find candidate tradeable Polymarket markets relevant to it. First "
                      "step for 'recommend a market to bet on <topic>'.",
                      frozenset({"question"}), frozenset({"candidates"}), run, cost=2)


def recommend_markets_capability(recommend_fn: Callable) -> Capability:
    """Goal-2 step 2: score the candidates and recommend the best with reasoning.

    ``recommend_fn(candidates) -> dict`` runs the analysis core (p_true / edge /
    action) on the top candidates, ranks by opportunity, and returns the pick plus
    the ranked shortlist. Reuses the same engine analysis that powers analyze_market.

    Also emits ``market_ref`` for the top pick, so a follow-up ``analyze_market``
    deep-dives THAT exact market (no re-resolve from the topic)."""
    def run(ctx: Context) -> dict:
        rec = recommend_fn(ctx.facts["candidates"])
        out = {"recommendation": rec}
        pick = rec.get("top_pick") if isinstance(rec, dict) else None
        if pick and pick.get("token_id"):               # hand the pick to analyze_market by token
            out["market_ref"] = {"token_id": pick["token_id"],
                                 "question": pick.get("question"), "price": pick.get("price")}
        return out
    return Capability("recommend_markets",
                      "Score the discovered candidate markets (true probability, edge, "
                      "action) and RECOMMEND the best trading target with reasons, plus a "
                      "ranked shortlist. Use after discover_markets for topic → recommendation.",
                      frozenset({"candidates"}), frozenset({"recommendation", "market_ref"}),
                      run, cost=4)


def strategy_capability(run_strategy_fn: Callable) -> Capability:
    """Wrap the multi-agent Strategy supervisor as one capability: market → decision.

    ``run_strategy_fn(market) -> decision`` (the supervisor's own data→signal→risk
    loop runs inside this single capability)."""
    def run(ctx: Context) -> dict:
        return {"decision": run_strategy_fn(ctx.facts["market"])}
    return Capability("strategy", "Run the data→signal→risk Strategy supervisor.",
                      frozenset({"market"}), frozenset({"decision"}), run, cost=4)


def build_registry(*, fetch_fn: Callable, backtest_fn: Callable | None = None,
                   signal_fn: Callable | None = None,
                   risk_fn: Callable | None = None) -> list[Capability]:
    """Assemble a capability registry from injected workers (a 'mode')."""
    reg = [data_capability(fetch_fn)]
    if backtest_fn:
        reg.append(backtest_capability(backtest_fn))
    if signal_fn:
        reg.append(signal_capability(signal_fn))
    if risk_fn:
        reg.append(risk_capability(risk_fn))
    return reg


def demo_registry() -> list[Capability]:
    """A fully-offline registry (deterministic fakes) so the loop is runnable and
    demoable without network. Real wiring (engine client + Lab BacktestRunner) is
    a thin follow-up — swap the fns in :func:`build_registry`."""
    def fetch(event):
        return {"event": event, "candles": [0.40, 0.45, 0.50, 0.55, 0.60]}

    def backtest(history):
        c = history["candles"]
        return {"event": history["event"], "n": len(c),
                "trend": round(c[-1] - c[0], 3), "verdict": "demo"}

    def signal(history):
        c = history["candles"]
        return {"p_true": min(0.98, max(0.02, c[-1]))}

    def risk(signal):
        return {"action": "buy" if signal["p_true"] > 0.5 else "hold"}

    return build_registry(fetch_fn=fetch, backtest_fn=backtest,
                          signal_fn=signal, risk_fn=risk)
