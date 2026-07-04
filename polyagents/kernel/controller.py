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
    "'Is this strategy/domain paper-ready / good enough to promote / does it pass the gates' "
    "→ promotion_gate.\n"
    "'Find mispriced crypto markets', crypto arbitrage, 'Will BTC be above $X', hunting "
    "trading opportunities in crypto → find_crypto_arb.\n"
    "'Find alpha', 'scan for opportunities across markets', 'what's worth trading right now' "
    "(broad, not one named market/topic) → hunt_alpha (the top-level opportunity scan).\n"
    "'Scan microstructure / order flow', 'where is the smart money' → microstructure_scan. "
    "'News / sentiment / headlines on X' → news_sentiment.\n"
    "'Do we have skill / are we beating the market / calibration report' → evaluate_skill. "
    "'Show my portfolio / P&L / positions / how are my trades doing' → portfolio_review.\n"
    "'Paper trade / take a position on / buy / sell a market (paper)' → resolve_market → "
    "paper_trade (only if that capability is loaded; it is gated).\n"
    "When a specialized scan (hunt_alpha / find_crypto_arb / microstructure_scan / "
    "news_sentiment) fits the request, call it and answer from its result — do NOT also call "
    "domain_answer for the same thing.\n"
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
    """Compact transcript of the recent conversation (bounded, for prompt context)."""
    lines = []
    for role, content in (history or [])[-max_turns:]:
        who = "User" if str(role) == "user" else "Assistant"
        lines.append(f"{who}: {_short(content, 300)}")
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
