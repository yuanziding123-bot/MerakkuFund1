"""Concrete sub-agents — thin wrappers over the existing engine.

Nothing here re-implements trading logic; each sub-agent adapts a slice of the
existing L1-L4 pipeline (or the deterministic risk math) to the blackboard
contract. They are intentionally minimal so the *design of each one can be
refined later* without touching the supervisor or the loop.

  * :class:`DataAgent`      — Layer 1 collection (needs a PolyAgentsGraph; network).
  * :class:`SignalAgent`    — Layer 2 probability read (needs a graph + LLM key).
  * :class:`RiskAgent`      — deterministic edge / Kelly / risk gates (no LLM, no net).
  * :class:`ExecutionAgent` — paper order from the risk decision (seam; dry-run default).

The graph-backed agents take the graph by injection so this module imports with
no network / LLM dependency (and so tests can pass fakes).
"""
from __future__ import annotations

from typing import Any

from .base import SubAgent
from .blackboard import AgentResult, Blackboard


class DataAgent(SubAgent):
    name = "data"
    description = ("Layer-1 market data: price, volume, order book, recent trade "
                  "flow, news and engineered factors for the target market.")

    def __init__(self, graph: Any) -> None:
        self.graph = graph                  # PolyAgentsGraph (injected)

    def run(self, bb: Blackboard) -> AgentResult:
        if bb.market is None:
            return self.fail("no market on the blackboard")
        state = self.graph.collect(bb.market)
        m = bb.market
        bb.data.update({
            "market_price": state.get("market_price", getattr(m, "price", None)),
            "liquidity": getattr(m, "liquidity", 0.0) or 0.0,
            "spread_bps": (state.get("raw", {}).get("orderbook", {}) or {}).get("spread_bps"),
            "days_to_expiry": getattr(m, "days_to_expiry", 30.0) or 30.0,
            "reports": {k: state.get(k) for k in (
                "price_report", "volume_report", "orderbook_report",
                "trades_flow_report", "news_report", "features_report") if state.get(k)},
        })
        return self.ok(f"collected L1 for “{getattr(m, 'question', m)}”",
                       market_price=bb.data["market_price"])


class SignalAgent(SubAgent):
    name = "signal"
    description = ("Layer-2 probability read: estimates the true probability the "
                 "outcome resolves YES from the collected factors and flow (LLM).")

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    def run(self, bb: Blackboard) -> AgentResult:
        if bb.market is None:
            return self.fail("no market on the blackboard")
        state = self.graph.analyze(bb.market)   # runs L1+L2; we harvest the Signal
        sig = state.get("signal")
        if sig is None:
            return self.fail("analysis produced no signal")
        price = state.get("market_price", bb.data.get("market_price"))
        bb.signal = {
            "direction": getattr(sig, "direction", "none"),
            "p_true": getattr(sig, "p_true", None),
            "conviction": getattr(sig, "conviction", "medium"),
            "rationale": getattr(sig, "rationale", ""),
            "market_price": price,
        }
        # let DataAgent-free runs still have the microstructure RiskAgent needs
        bb.data.setdefault("market_price", price)
        bb.data.setdefault("liquidity", float(state.get("liquidity", 0.0) or 0.0))
        bb.data.setdefault("days_to_expiry", float(state.get("days_to_expiry", 30.0) or 30.0))
        bb.data.setdefault("spread_bps",
                           (state.get("raw", {}).get("orderbook", {}) or {}).get("spread_bps"))
        return self.ok(f"p_true {bb.signal['p_true']} ({bb.signal['direction']}, "
                       f"{bb.signal['conviction']})")


class RiskAgent(SubAgent):
    name = "risk"
    description = ("Deterministic sizing: calibrate the signal, compute edge and "
                 "time-annualised return, apply liquidity/spread/APY gates and "
                 "quarter-Kelly sizing. No LLM — auditable risk math.")

    def __init__(self, config: dict) -> None:
        self.config = config

    def run(self, bb: Blackboard) -> AgentResult:
        from polyagents.agents.decision_agent import decide
        from polyagents.agents.schemas import Signal

        if not bb.signal or bb.signal.get("p_true") is None:
            return self.fail("no signal to size")
        price = bb.signal.get("market_price") or bb.data.get("market_price")
        if price is None:
            return self.fail("no market price available")
        sig = Signal(direction=bb.signal["direction"], p_true=float(bb.signal["p_true"]),
                     conviction=bb.signal.get("conviction", "medium"),
                     rationale=bb.signal.get("rationale", "") or "n/a")
        d = decide(sig, market_price=float(price),
                   liquidity=float(bb.data.get("liquidity", 0.0) or 0.0),
                   spread_bps=bb.data.get("spread_bps"),
                   config=self.config,
                   days_to_expiry=float(bb.data.get("days_to_expiry", 30.0) or 30.0))
        bb.risk = {"action": d.action, "size_usdc": d.size_usdc, "edge": d.edge,
                   "p_cal": d.p_true, "apy": d.annualized_edge,
                   "kelly_fraction": d.kelly_fraction, "reasons": d.reasons}
        summary = f"{d.action.upper()} · edge {d.edge:+.1%} · size ${d.size_usdc:,.0f}"
        # a HOLD ends the strategy here — nothing to execute, but it's a success
        return AgentResult(self.name, ok=True, summary=summary, output=bb.risk,
                           halt=(d.action == "hold"))


class ExecutionAgent(SubAgent):
    name = "execution"
    description = ("Place the paper order implied by the risk decision through the "
                 "circuit breaker and update the portfolio.")

    def __init__(self, graph: Any = None) -> None:
        self.graph = graph

    def run(self, bb: Blackboard) -> AgentResult:
        if not bb.risk:
            return self.fail("no risk decision to execute")
        action = bb.risk.get("action")
        if action not in ("buy", "sell"):
            return self.fail(f"nothing to execute (action={action})", halt=False)
        # Seam: a full execution sub-agent will place the order via the graph's
        # execution client / portfolio. For now record the intended order so the
        # loop is end-to-end; flip `graph.trade` in here when wiring live paper.
        bb.execution = {"intended": True, "action": action,
                        "size_usdc": bb.risk.get("size_usdc")}
        return self.ok(f"order staged: {action} ${bb.risk.get('size_usdc'):,.0f} (dry-run)")
