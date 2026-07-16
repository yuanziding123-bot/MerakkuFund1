"""The kernel's LLM controller — the brain that decides, each step, *how* to
answer: call a sub-agent/tool, or answer the user directly.

This is the "complete kernel" the mentor asked for: not a fixed pipeline and not a
keyword router, but an LLM-driven loop over a capability registry. Each turn the
model sees the request, the facts gathered so far, and the capabilities runnable
now, then picks the next action. The deterministic goal-directed planner still
exists as a *shortcut* for known chains (data→backtest); the controller is the
primary driver for open-ended requests.

Injected ``llm`` (``.invoke(messages) -> obj with .content``), so it's unit-
testable with a scripted fake — no network in the core.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .core import Capability, Context, Goal, Step

_SYS = (
    "You are the controller of an agent loop for a prediction-market research "
    "assistant. Each turn you EITHER call one capability (a sub-agent or tool) to "
    "gather what you still need, OR give the final answer to the user. Prefer the "
    "fewest steps: only call a capability when you actually need its result; if you "
    "can already answer, answer.\n"
    "When the user asks you to DO something (run / collect / 采集 / 批量 / backtest / "
    "scan and persist / analyze a market / 分析), call the matching ACTION capability and "
    "actually perform it — do NOT answer with a generic Q&A capability (langgraph_answer / "
    "domain_answer) when a specialized capability fits. Chain steps when one produces what "
    "the next needs (e.g. scan_markets → batch_collect, resolve_market → analyze_market, "
    "discover_markets → recommend_markets).\n"
    "'Backtest a signal / one strategy over a domain' → batch_backtest. 'Compare / backtest "
    "the (multiple) strategies for a domain', 'which strategy works' → backtest_strategies. "
    "'Which strategy works in which domain', 'full strategy sweep', 'backtest matrix' "
    "(strategies ACROSS all domains) → backtest_matrix. "
    "'Is this strategy/domain paper-ready / good enough to promote / does it pass the gates' "
    "→ promotion_gate.\n"
    "'Backfill / label outcomes on the stored snapshots', 'prepare the Lab data' → "
    "backfill_outcomes. 'Run the LAB backtest', 'backtest strategy X with the Lab evidence "
    "path / feature strategies' → lab_backtest (the feature-bundle backtest → EvaluationReport "
    "+ gates, distinct from candle-signal backtest_strategies; both need the lab-backtest pack "
    "loaded). Typical flow: backfill_outcomes THEN lab_backtest.\n"
    "'Log/record MY OWN probability call', '记录我对X的预测:30% / 我觉得X概率是Y / 我押X' → "
    "log_prediction. 'Show my prediction journal / my calibration / am I beating the market / "
    "看我的预测日志 / 我的判断准不准 / 我在哪类市场有 edge' → prediction_journal. Both need the "
    "prediction-journal pack.\n"
    "'What changed today / what's moving / where do I start looking', '有什么异动 / 快到期的 / "
    "新上市的市场 / 市场雷达 / 从哪找机会' → market_radar (surfaces movers + near-resolution + "
    "fresh markets as leads; needs the market-radar pack). It finds candidates, not verdicts.\n"
    "'Scan for conditional / cross-market / champion-vs-advance arbitrage', '找条件概率套利 / "
    "跨市场套利 / 晋级-夺冠套利 / 冠军比进决赛还贵吗' → scan_conditional_arb (sweeps entities whose "
    "championship market links to advance/reach-final/match markets; flags risk-free implication "
    "violations, needs the conditional-arb pack). Distinct from find_crypto_arb (spot vs implied).\n"
    "'Find mispriced crypto markets', crypto arbitrage, 'Will BTC be above $X', hunting "
    "trading opportunities in crypto → find_crypto_arb.\n"
    "'Find alpha', 'scan for opportunities across markets', 'what's worth trading right now' "
    "(broad, not one named market/topic) → hunt_alpha (the top-level opportunity scan).\n"
    "'What should I trade / buy now', 'give me trade signals / actionable ideas with sizing', "
    "'run the monitor', 'scan markets with strategy X' → scan_opportunities (Lab monitor: "
    "scores live markets with a strategy → ranked buy/sell/hold + edge + size, dry-run). "
    "Prefer this over hunt_alpha when the user wants concrete SIZED actions, not just mispricings.\n"
    "'Plot / chart / visualize / draw', '画出…的价格走势 / 走势图 / 画个图 / 把…可视化 / 用柱状图/面积图 / "
    "对比…的走势' → plot_market (renders a picture: line/area price trend, multi-line comparison, "
    "or bar of current prices). Use whenever the user wants a CHART/图, not a table of numbers.\n"
    "'Validate my strategy / thesis has alpha', 'is my idea any good + how to improve it', "
    "'验证我的策略有没有 alpha / 帮我改进策略', a named strategy (hold / 短线 / 套利) on a target, "
    "'<target> 有没有 alpha / 有没有机会' → research_alpha (formalize + relational evidence + verdict + "
    "improvements). 'Is <team> underpriced vs the field', 'how does <other event/match> affect "
    "<target>', '事件关联性 / 别的场次对这场的影响 / 冠军集里谁被低估' → relational_alpha (the computed "
    "cross-event engine). When the alpha-research pack is loaded and the request is about ALPHA / an "
    "OPPORTUNITY / a STRATEGY on a target, call research_alpha or relational_alpha DIRECTLY (they "
    "resolve the target internally) — do NOT also call analyze_market for the same request. "
    "analyze_market is only for a plain 'analyze this market' with no alpha/strategy framing.\n"
    "'Scan microstructure / order flow', 'where is the smart money' → microstructure_scan "
    "(NOT hunt_alpha, NOT domain_answer). 'News / sentiment / headlines on X' → news_sentiment.\n"
    "'This NEWS/event — which markets does it affect / 这条新闻影响哪些标的 / 利好利空哪些市场 / "
    "<某事件>会影响哪些盘' → news_to_markets (reverse of news_sentiment: news → affected markets + "
    "direction; needs the news-events pack).\n"
    "'Run the data→signal→risk supervisor / multi-agent strategy / give me a decision on a "
    "market' → resolve_market → strategy (when that capability is loaded).\n"
    "'Do we have skill / are we beating the market / calibration report' → evaluate_skill. "
    "'Show my portfolio / P&L / positions / how are my trades doing' → portfolio_review.\n"
    "'Paper trade / take a position on / buy / sell a market (paper)' → resolve_market → "
    "paper_trade (only if that capability is loaded; it is gated).\n"
    "'Settle my trades / close resolved positions / what did we learn' → settle_and_reflect.\n"
    "When a specialized capability (hunt_alpha / scan_opportunities / find_crypto_arb / "
    "microstructure_scan / news_sentiment / strategy) fits the request, call exactly that ONE "
    "and answer from its "
    "result — do NOT also call domain_answer or another scan for the same thing.\n"
    "ANALYZING / evaluating a market — a named one OR 'the most active market' / 'a liquid "
    "one' / 'this market' — goes through resolve_market → analyze_market (resolve_market "
    "handles 'most active'). discover_markets is ONLY for a THEME / event / hot topic when "
    "the user wants you to FIND candidate markets to recommend — never for an 'analyze' "
    "request. A theme with 'recommend a market to bet on' goes discover_markets → "
    "recommend_markets. After discover_markets you MUST call "
    "recommend_markets next (NEVER resolve_market) — discovering candidates exists only "
    "to feed recommend_markets, which scores and ranks them.\n"
    "Analyzing / evaluating a specific market or trading target MUST go through "
    "resolve_market → analyze_market (the full framework), never a Q&A capability. After "
    "recommend_markets you MAY call analyze_market to deep-dive the recommended pick "
    "(it reuses the pick's market_ref) — do so if the user wanted analysis, otherwise the "
    "recommendation itself is enough.\n"
    "Stay grounded: use ONLY numbers and facts present in the gathered facts. Never invent "
    "prices, probabilities, standings, or event knowledge, and never draw a conclusion about "
    "a market you did not actually analyze — if the user named another market, say it needs "
    "its own analyze_market run. Follow the microstructure discipline (track the flow, don't "
    "predict the event).\n"
    "Reply with ONLY one JSON object, nothing else:\n"
    '  {"action": "call", "capability": "<name from the menu>"}\n'
    '  {"action": "final", "answer": "<the answer to the user>"}'
)


@dataclass
class KernelResult:
    """What the controller loop produced."""
    answer: str
    facts: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)   # Step per capability call
    steps: int = 0
    notes: list = field(default_factory=list)   # the step-by-step scratchpad
    llm_ok: bool = True                          # did the LLM ever make a usable decision?


def _text_of(resp: Any) -> str:
    text = getattr(resp, "content", resp)
    if isinstance(text, list):
        text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
    return str(text)


def _parse(text: str) -> dict:
    """Pull the first JSON object out of the model's reply (tolerant of prose)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _short(v: Any, n: int = 160) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…"


def _render_history(history, max_turns: int = 8) -> str:
    """Compact transcript of the recent conversation (bounded, for prompt context).

    The two most recent turns keep a much larger budget so a follow-up can actually
    reference the prior result (e.g. 'backtest the strategies you just found') — a
    kernel result board runs ~1–2k chars and was previously cut to 300."""
    turns = list(history or [])[-max_turns:]
    lines = []
    for i, (role, content) in enumerate(turns):
        who = "User" if str(role) == "user" else "Assistant"
        cap = 1600 if i >= len(turns) - 2 else 400      # recent turns fuller, older ones compact
        lines.append(f"{who}: {_short(content, cap)}")
    return "\n".join(lines)


class KernelController:
    """LLM-driven controller over a capability registry.

    ``run(request, **facts)`` seeds the blackboard, then loops: ask the model for
    the next action, run the chosen capability (feeding its result back), until the
    model answers or the step budget runs out. Emits the same event shapes as
    :class:`~polyagents.kernel.core.AgentLoop` so the web layer can stream it.
    """

    def __init__(self, registry: list[Capability], llm, *, max_steps: int = 8,
                 on_event: Callable[[dict], None] | None = None, audit=None) -> None:
        self.registry = list(registry)
        self.llm = llm
        self.max_steps = max_steps
        self.on_event = on_event
        self.audit = audit
        self._history: list = []

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def _audit(self, event_type: str, **payload) -> None:
        if self.audit is not None:
            try:
                self.audit.log("kernel", event_type, payload, mode="controller")
            except Exception:
                pass

    def _runnable(self, ctx: Context) -> list[Capability]:
        return [c for c in self.registry
                if c.preconditions <= ctx.known and not (c.effects <= ctx.known)]

    def _decide(self, ctx: Context, request: str, runnable: list[Capability],
                notes: list[str]) -> dict:
        menu = "\n".join(
            f"- {c.name}: {c.description} (produces {sorted(c.effects)})"
            for c in runnable) or "- (none)"
        facts = "\n".join(f"- {k}: {_short(v)}" for k, v in ctx.facts.items()
                          if k != "question") or "- (none)"
        steps = "\n".join(notes) or "- (none yet)"
        convo = _render_history(self._history)
        prefix = f"Conversation so far:\n{convo}\n\n" if convo else ""
        user = (f"{prefix}User request: {request}\n\nFacts gathered so far:\n{facts}\n\n"
                f"Capabilities you can call now:\n{menu}\n\nSteps so far:\n{steps}\n\n"
                "Next action? Resolve references to earlier turns from the conversation. "
                "JSON only.")
        try:
            return _parse(_text_of(self.llm.invoke([("system", _SYS), ("user", user)])))
        except Exception:
            return {}

    def run(self, request: str, *, history=None, **facts) -> KernelResult:
        self._history = list(history or [])               # prior conversation turns
        ctx = Context(Goal(frozenset(), {"question": request, **facts}, "kernel"))
        notes: list[str] = []
        decided = 0                                       # usable LLM decisions seen
        self._emit({"type": "loop.start", "goal": [], "label": "kernel"})
        self._audit("loop.start", label="kernel", request=_short(request))
        for i in range(self.max_steps):
            runnable = self._runnable(ctx)
            decision = self._decide(ctx, request, runnable, notes)
            action = str(decision.get("action", "")).lower()
            if action == "final" or (action != "call" and not runnable):
                answer = str(decision.get("answer", "")).strip()
                if answer:
                    return self._finish(ctx, answer, i, notes, llm_ok=True)
            if action == "call":
                name = str(decision.get("capability", "")).strip()
                cap = next((c for c in runnable if c.name == name), None)
                if cap is None:
                    notes.append(f"- tried to call '{name}' but it is not available now")
                    continue
                decided += 1
                self._emit({"type": "capability.start", "name": cap.name})
                try:
                    if cap.stream is not None and self.on_event is not None:
                        produced = cap.stream(ctx, self.on_event) or {}   # inner tokens flow out live
                    else:
                        produced = cap.run(ctx) or {}
                except Exception as exc:                      # surface, let the model re-plan
                    ctx.trace.append(Step(cap.name, [], ok=False, note=str(exc)))
                    notes.append(f"- {cap.name} failed: {exc}")
                    self._audit("capability.error", capability=cap.name, error=str(exc))
                    self._emit({"type": "capability.error", "name": cap.name, "error": str(exc)})
                    continue
                ctx.facts.update(produced)
                keys = list(produced)
                ctx.trace.append(Step(cap.name, keys, ok=True))
                notes.append(f"- called {cap.name} -> {keys}")
                self._audit("capability.ran", capability=cap.name, produced=keys)
                self._emit({"type": "capability.done", "name": cap.name, "produced": keys})
                continue
            # neither a valid call nor a final answer -> nudge once more, then bail
            notes.append("- (no decision; must call a capability or give final answer)")
        forced, forced_ok = self._forced_answer(ctx, request)
        return self._finish(ctx, forced, self.max_steps, notes,
                            llm_ok=bool(decided) or forced_ok)

    def _forced_answer(self, ctx: Context, request: str) -> tuple[str, bool]:
        """Budget exhausted — make the model answer from whatever we gathered.
        Returns ``(answer, ok)`` where ok is False if the LLM was unusable."""
        facts = "\n".join(f"- {k}: {_short(v)}" for k, v in ctx.facts.items() if k != "question")
        convo = _render_history(getattr(self, "_history", []))
        prefix = f"Conversation so far:\n{convo}\n\n" if convo else ""
        user = (f"{prefix}User request: {request}\n\nFacts gathered:\n{facts or '- (none)'}\n\n"
                "Give the final answer to the user now (plain text).")
        try:
            text = _text_of(self.llm.invoke([("system", _SYS), ("user", user)])).strip()
            return (text, True) if text else ("(no answer)", False)
        except Exception:
            return "(no answer)", False

    def _finish(self, ctx: Context, answer: str, steps: int, notes: list,
                *, llm_ok: bool) -> KernelResult:
        ctx.facts["answer"] = answer
        self._audit("loop.end", steps=steps, path=[s.capability for s in ctx.trace])
        self._emit({"type": "loop.end", "done": True,
                    "path": [s.capability for s in ctx.trace]})
        return KernelResult(answer=answer, facts=ctx.facts, trace=ctx.trace, steps=steps,
                            notes=notes, llm_ok=llm_ok)
