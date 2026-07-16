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


def promotion_gate_capability(fn: Callable) -> Capability:
    """Apply Lab's promotion gates to a domain's strategies — is any PAPER-READY?

    ``fn(query) -> dict`` backtests the strategies over the domain's resolved markets
    and runs the Lab gates (sample adequate + beats market + ECE calibration + PIT
    clean) on each, reporting which (if any) passes. This is the loop→Lab bridge:
    it reuses Lab's ``promotion_gates`` to turn a backtest into a go/no-go verdict."""
    def run(ctx: Context) -> dict:
        return {"promotion_verdict": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("promotion_gate",
                      "Check whether a strategy / domain is PAPER-READY using Lab's "
                      "promotion gates (enough samples + beats market + calibration/ECE + "
                      "point-in-time clean). Use for 'is this good enough to go to paper / "
                      "promote', 'which gate does it fail'.",
                      frozenset({"question"}), frozenset({"promotion_verdict"}), run, cost=4)


def settle_and_reflect_capability(fn: Callable) -> Capability:
    """Settle resolved paper trades + Layer-4 reflection (pack: paper-exec).

    ``fn(query) -> dict`` settles any paper position whose market has resolved (books
    $1/$0 payout and realised P&L) and writes a reflection lesson per trade — closing
    the feedback loop so evaluate_skill has data and future signals learn."""
    def run(ctx: Context) -> dict:
        return {"settlement": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("settle_and_reflect",
                      "Settle resolved PAPER trades (book P&L) and reflect on each outcome "
                      "(what the signal got right/wrong → a lesson). Use for 'settle my trades', "
                      "'close resolved positions', 'what did we learn'.",
                      frozenset({"question"}), frozenset({"settlement"}), run, cost=3)


def paper_trade_capability(fn: Callable) -> Capability:
    """Paper-trade a market — the loop's one 'act' capability (pack: paper-exec, gated).

    ``fn(market_ref) -> dict`` analyses the market, takes the deterministic sized/risk-
    gated decision, and — only if it's an actionable buy/sell — places a PAPER order
    through the circuit breaker, updating the paper portfolio. Most markets are efficient
    so the honest result is usually HOLD (no trade). Paper money only."""
    def run(ctx: Context) -> dict:
        return {"paper_trade": fn(ctx.facts["market_ref"])}
    return Capability("paper_trade",
                      "PAPER-trade a specific market: size + risk-gate the decision and, if "
                      "actionable (buy/sell), place a paper order through the circuit breaker. "
                      "Paper money only. Use for 'paper trade X', 'take a position on X', 'buy/"
                      "sell X (paper)'. Needs a resolved market first (resolve_market/analyze).",
                      frozenset({"market_ref"}), frozenset({"paper_trade"}), run, cost=4)


def evaluate_skill_capability(fn: Callable) -> Capability:
    """Calibration / skill report — does our p_cal actually beat the market baseline?

    ``fn(query) -> dict`` runs the evaluation over the decision log (Brier / log-loss /
    ECE vs the market price, by category). If we don't beat the market, the edge is
    noise — this is the honest 'do we have any skill' check."""
    def run(ctx: Context) -> dict:
        return {"skill_report": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("evaluate_skill",
                      "Report whether our forecasts beat the market baseline over time "
                      "(Brier / calibration / ECE, by category) — 'do we actually have skill / "
                      "alpha', 'calibration report', 'are we beating the market'.",
                      frozenset({"question"}), frozenset({"skill_report"}), run, cost=2)


def portfolio_review_capability(fn: Callable) -> Capability:
    """Paper portfolio + P&L review. ``fn(query) -> dict`` returns cash / exposure /
    positions plus the realised-P&L / hit-rate / attribution report."""
    def run(ctx: Context) -> dict:
        return {"portfolio_review": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("portfolio_review",
                      "Show the paper portfolio and P&L: cash, open positions, exposure, "
                      "realised P&L, hit rate and attribution. Use for 'show my portfolio / "
                      "P&L / positions / how are my trades doing'.",
                      frozenset({"question"}), frozenset({"portfolio_review"}), run, cost=1)


def news_sentiment_capability(fn: Callable) -> Capability:
    """News + sentiment for a market/topic — an event-driven signal (pack: news-events).

    ``fn(query) -> dict`` searches recent news for the query and scores each item's
    sentiment, aggregating a bullish/bearish read. Needs TAVILY_API_KEY; degrades
    gracefully when unset."""
    def run(ctx: Context) -> dict:
        return {"news_sentiment": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("news_sentiment",
                      "Pull recent NEWS for a market/topic and score its sentiment "
                      "(bullish/bearish) — an event-driven signal. Use for 'what's the news / "
                      "sentiment on X', 'any headlines moving this market'.",
                      frozenset({"question"}), frozenset({"news_sentiment"}), run, cost=3)


def microstructure_scan_capability(fn: Callable) -> Capability:
    """Scan live order-book microstructure / smart-money flow across markets (pack:
    microstructure). ``fn(query) -> dict`` collects L1 microstructure for a batch of
    markets and ranks them by flow/book conviction vs a lagging price — the deep,
    dedicated version of hunt_alpha's flow section."""
    def run(ctx: Context) -> dict:
        return {"microstructure": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("microstructure_scan",
                      "Scan order-book microstructure and trade-flow across a BATCH of markets "
                      "(optionally a category) — micro-price, depth imbalance, book pressure, "
                      "flow imbalance — and rank where smart money leads a lagging price. Use "
                      "for 'scan microstructure / order flow', 'where is the smart money'.",
                      frozenset({"question"}), frozenset({"microstructure"}), run, cost=4)


def hunt_alpha_capability(fn: Callable) -> Capability:
    """Top-level opportunity hunt — one request → scan the universe → consolidated board.

    ``fn(query) -> dict`` runs the deterministic edge detectors across market types
    (crypto spot-vs-implied mispricing + microstructure / smart-money flow) and returns
    a ranked opportunity report. This is the 'batch-automate and give me results' entry
    point: it orchestrates several scanners into one honest board."""
    def run(ctx: Context) -> dict:
        return {"alpha_hunt": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("hunt_alpha",
                      "Hunt for trading opportunities ACROSS the market universe in one go: "
                      "scan crypto spot-vs-implied mispricings and microstructure / smart-money "
                      "flow signals, then rank them into one opportunity board. Use for 'find "
                      "alpha', 'scan for opportunities', 'what's worth trading right now'.",
                      frozenset({"question"}), frozenset({"alpha_hunt"}), run, cost=5)


def scan_opportunities_capability(fn: Callable) -> Capability:
    """Dry-run opportunity monitor — score live markets with a Lab strategy and rank
    actionable trades (Lab's ``LabMonitor.scan``). ``fn(query) -> dict`` builds a live
    read-only feature bundle per active market, scores it with the strategy's factor
    model, sizes a paper position through the risk gate, and returns a ranked board of
    (action, edge, size) — always dry-run. This is the Ask-side 'what's actually worth
    trading right now' scan, backed by the Lab strategy library."""
    def run(ctx: Context) -> dict:
        return {"opportunities": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("scan_opportunities",
                      "Scan live active markets with a Lab strategy and rank concrete, "
                      "actionable DRY-RUN trades — each with action (buy/sell/hold), edge, "
                      "sized paper position, and reasons. Use for 'what should I trade now', "
                      "'scan for trade signals / opportunities to buy', 'run the monitor'. "
                      "(Read-only, no orders; the strategy-scored complement to hunt_alpha.)",
                      frozenset({"question"}), frozenset({"opportunities"}), run, cost=6)


def relational_alpha_capability(fn: Callable) -> Capability:
    """Event-relatedness engine (pack: alpha-research). ``fn(query) -> dict`` builds the
    target's mutually-exclusive winner set, checks field consistency (Σ prices vs 1),
    computes redistribution + a lag signal (a rival crashed but the target hasn't repriced
    → underpriced), and a what-if sensitivity (if rival X is eliminated → target fair prob).
    Deterministic, computed from live prices + candle history."""
    def run(ctx: Context) -> dict:
        return {"relational_alpha": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("relational_alpha",
                      "Cross-event / relational analysis of a target market: winner-set "
                      "consistency, redistribution + lag detection (a related event moved but "
                      "the target hasn't → edge), and what-if sensitivity to rivals. Use for "
                      "'is <team> underpriced vs the field', 'how does <other event> affect <target>', "
                      "'关联/事件关联性/别的场次对这场的影响'. Computed, no fabrication.",
                      frozenset({"question"}), frozenset({"relational_alpha"}), run, cost=5)


def research_alpha_capability(fn: Callable) -> Capability:
    """Strategy alpha review (pack: alpha-research). ``fn(query) -> dict`` runs the relational
    engine + news, then judges whether the user's thesis has alpha and proposes concrete
    improvements — grounded strictly in the computed numbers. The Ask-side 'validate my
    strategy + tell me how to improve it' deliverable."""
    def run(ctx: Context) -> dict:
        return {"alpha_review": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("research_alpha",
                      "Validate a trading THESIS/STRATEGY the user proposes and suggest "
                      "improvements: gather relational (cross-event) evidence + news, judge if "
                      "it has alpha with the numbers, and give concrete, data-grounded fixes. Use "
                      "for '验证我的策略有没有 alpha / 帮我改进策略 / research whether <thesis> has edge'.",
                      frozenset({"question"}), frozenset({"alpha_review"}), run, cost=7)


def news_to_markets_capability(fn: Callable) -> Capability:
    """News → affected markets (pack: news-events) — reverse of news_sentiment.
    ``fn(query) -> dict`` entity-links a news item to live Polymarket markets and rates
    each one's likely direction for YES. The event-driven scouting tool."""
    def run(ctx: Context) -> dict:
        return {"news_markets": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("news_to_markets",
                      "Given a NEWS item / headline / event, find which live Polymarket markets it "
                      "affects and the likely direction (📈利好/📉利空) for each, with why. The reverse "
                      "of news_sentiment. Use for '这条新闻影响哪些标的 / 利好利空哪些市场 / news → markets "
                      "/ <某事件>会影响哪些盘'. Needs the news-events pack.",
                      frozenset({"question"}), frozenset({"news_markets"}), run, cost=4)


def log_prediction_capability(fn: Callable) -> Capability:
    """Log the user's own subjective probability call (pack: prediction-journal).
    ``fn(query) -> dict`` parses the %/decimal, resolves the market, and records
    (your P, the market price now) to the shared journal for later scoring."""
    def run(ctx: Context) -> dict:
        return {"prediction_logged": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("log_prediction",
                      "Record the USER'S OWN probability estimate for a market (their subjective "
                      "call + the market price now), to score later. Use for '记录我对X的预测:30% / "
                      "我觉得X概率是Y / log my call / 我押X'. Needs the prediction-journal pack.",
                      frozenset({"question"}), frozenset({"prediction_logged"}), run, cost=2)


def prediction_journal_capability(fn: Callable) -> Capability:
    """Show the prediction journal + personal calibration (pack: prediction-journal).
    ``fn(query) -> dict`` auto-settles resolved calls (Brier you vs market), lists open
    calls, and aggregates where your subjective read beats the market (overall/by category)."""
    def run(ctx: Context) -> dict:
        return {"prediction_journal": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("prediction_journal",
                      "Show your prediction journal + personal calibration: open calls with current "
                      "edge, resolved calls scored (Brier you vs market), and where your judgment "
                      "has edge (overall + by category). Use for '看我的预测日志 / 我的判断准不准 / "
                      "我在哪类市场有 edge / show my journal / my calibration'. Needs the pack.",
                      frozenset({"question"}), frozenset({"prediction_journal"}), run, cost=3)


def market_radar_capability(fn: Callable) -> Capability:
    """Market radar (pack: market-radar) — 'what changed today'. ``fn(query) -> dict``
    sweeps live markets and surfaces leads for a human to dig into: biggest recent price
    movers, markets near resolution, and short-history (possibly newly-listed / thin)
    markets. No verdicts — a discovery funnel for subjective alpha hunting."""
    def run(ctx: Context) -> dict:
        return {"market_radar": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("market_radar",
                      "Scan the market for LEADS to dig into: biggest recent movers (price % "
                      "change), markets near resolution (endgame), and short-history / newly-listed "
                      "markets. Use for 'what changed today / what's moving / 有什么异动 / 快到期的 / "
                      "新上市的市场 / 市场雷达 / 从哪找机会'. Surfaces candidates, does not decide.",
                      frozenset({"question"}), frozenset({"market_radar"}), run, cost=6)


def scan_conditional_arb_capability(fn: Callable) -> Capability:
    """Cross-market conditional / implication arbitrage scanner (pack: conditional-arb).
    ``fn(query) -> dict`` sweeps the market for entities whose championship market links to
    lower-stage (reach-final / advance / single-match) markets, computes the implied
    conditional P(champ|advance), and flags GENUINE logical-implication arbitrage (a
    stronger claim priced above a weaker one — risk-free, bounded), kept separate from
    directional conditional value. Computed, honest about what is truly risk-free."""
    def run(ctx: Context) -> dict:
        return {"conditional_arb": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("scan_conditional_arb",
                      "Scan for cross-market CONDITIONAL / logical-implication arbitrage: link an "
                      "entity's championship market to its advance-this-round / reach-final / match "
                      "markets, report P(champ|advance), and flag risk-free implication violations "
                      "(stronger claim priced above weaker). Use for '找条件概率/跨市场/晋级-夺冠套利', "
                      "'scan for conditional arbitrage', 'where is champ priced above advancing'.",
                      frozenset({"question"}), frozenset({"conditional_arb"}), run, cost=5)


def plot_market_capability(fn: Callable) -> Capability:
    """Visualize market data as a chart (core, always-on).

    ``fn(query) -> dict`` picks the chart type + target from the request and returns a
    chart spec (series of points / bars) that the web layer renders as an inline SVG —
    price trend over time (line / area), several markets compared (multi-line), or a
    snapshot bar chart of current prices."""
    def run(ctx: Context) -> dict:
        return {"chart": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("plot_market",
                      "Draw a CHART of market data as an inline SVG: a market's price trend "
                      "over time (line/area), several markets compared (multi-line), or a bar "
                      "chart of current prices. Use for 'plot / chart / visualize / 画图 / "
                      "画出…的价格走势 / 走势图 / 把…可视化 / 对比…的走势'. Renders a picture, not a table.",
                      frozenset({"question"}), frozenset({"chart"}), run, cost=3)


def backfill_outcomes_capability(fn: Callable) -> Capability:
    """Label stored market snapshots with their realised outcome (pack: lab-backtest).

    ``fn(query) -> dict`` walks the collection cache in the shared data store (cloud
    Postgres when POLYAGENTS_DATABASE_URL is set, else local SQLite), looks up which of
    those markets have since resolved, and writes ``lab.outcome`` (0/1) back — turning
    accumulated snapshots into a labelled backtest set the Lab strategies can score."""
    def run(ctx: Context) -> dict:
        return {"outcome_backfill": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("backfill_outcomes",
                      "Backfill realised outcomes onto stored market snapshots: find which "
                      "collected markets have resolved and label them (writes to the shared "
                      "cloud DB). Run this to feed the Lab backtest with real data. Use for "
                      "'backfill outcomes', 'label the collected snapshots', 'prepare Lab data'.",
                      frozenset({"question"}), frozenset({"outcome_backfill"}), run, cost=4)


def lab_backtest_capability(fn: Callable) -> Capability:
    """Run a Lab feature-strategy over the labelled snapshots (pack: lab-backtest).

    ``fn(query) -> dict`` runs the colleague's ``BacktestRunner.run`` evidence path — a
    chosen Lab strategy (linear-factor / momentum / flow / sentiment / …) scored over the
    stored collection bundles with resolved outcomes — and returns the EvaluationReport
    metrics + promotion gates. Needs backfill_outcomes to have labelled data first, else
    it honestly reports it fell back to fixtures."""
    def run(ctx: Context) -> dict:
        return {"lab_backtest": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("lab_backtest",
                      "Run the Lab's feature-based backtest: score a Lab strategy over the "
                      "labelled market snapshots and return Brier vs market + calibration + "
                      "promotion gates (paper-ready?). The deep evidence backtest, distinct "
                      "from the candle-signal backtest_strategies. Use for 'run the Lab "
                      "backtest', 'backtest strategy X with the Lab evidence path'.",
                      frozenset({"question"}), frozenset({"lab_backtest"}), run, cost=6)


def crypto_arb_capability(fn: Callable) -> Capability:
    """Cross-market crypto arbitrage — the cross-market-arb strategy as a loop capability.

    ``fn(query) -> dict`` scans crypto threshold markets ('Will BTC be above $X?'),
    estimates each YES probability from the live exchange spot + volatility, compares
    to the market's implied price, and ranks the mispricings. This is the one genuine
    alpha strategy wired into the loop."""
    def run(ctx: Context) -> dict:
        return {"crypto_arb": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("find_crypto_arb",
                      "Find mispriced / lagging Polymarket CRYPTO markets ('Will BTC be "
                      "above $X?') by comparing the live exchange SPOT price + volatility to "
                      "the market's implied probability. Use for crypto arbitrage, 'find "
                      "mispriced crypto markets', or hunting valuable trading opportunities.",
                      frozenset({"question"}), frozenset({"crypto_arb"}), run, cost=3)


def backtest_matrix_capability(fn: Callable) -> Capability:
    """Strategy × domain backtest matrix (pack: backtest-lab).

    ``fn(query) -> dict`` backtests every strategy signal over every market category's
    resolved markets in one pass, returning a matrix of brier_delta / beats-market per
    (strategy, domain) cell plus any winners — 'which strategy works where'."""
    def run(ctx: Context) -> dict:
        return {"backtest_matrix": fn(ctx.facts.get("question") or ctx.facts.get("event"))}
    return Capability("backtest_matrix",
                      "Backtest EVERY strategy across EVERY market domain at once → a matrix "
                      "of which (strategy, domain) combos beat the market. Use for 'which "
                      "strategy works in which domain', 'full strategy sweep', 'backtest matrix'.",
                      frozenset({"question"}), frozenset({"backtest_matrix"}), run, cost=5)


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
