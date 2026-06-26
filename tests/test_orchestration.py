"""Main/sub-agent framework: the loop, routing, and the deterministic RiskAgent.

All offline — graph-backed sub-agents are exercised with fakes; RiskAgent runs
the real decision math.
"""
from __future__ import annotations

from polyagents.default_config import DEFAULT_CONFIG
from polyagents.orchestration import (
    Blackboard, CallbackRouter, LLMRouter, RiskAgent, SequentialRouter,
    Supervisor, build_supervisor, run_strategy,
)
from polyagents.orchestration.base import SubAgent
from polyagents.orchestration.blackboard import AgentResult


# ----- fakes ----------------------------------------------------------------

class _Spy(SubAgent):
    """A sub-agent that records it ran; can be told to halt or raise."""
    def __init__(self, name, *, halt=False, boom=False):
        self.name = name
        self.description = f"spy {name}"
        self._halt = halt
        self._boom = boom

    def run(self, bb):
        if self._boom:
            raise RuntimeError("kaboom")
        bb.notes.append(self.name)
        return AgentResult(self.name, ok=True, summary=f"{self.name} ran", halt=self._halt)


class _FakeMarket:
    question = "Will X happen?"
    price = 0.40
    liquidity = 50_000.0
    days_to_expiry = 10.0


class _FakeGraph:
    """Stand-in for PolyAgentsGraph: returns canned L1 / L2 state."""
    def collect(self, market):
        return {"market_price": 0.40,
                "price_report": "px", "raw": {"orderbook": {"spread_bps": 30}}}

    def analyze(self, market):
        from polyagents.agents.schemas import Signal
        return {"market_price": 0.40, "liquidity": 50_000.0, "days_to_expiry": 10.0,
                "raw": {"orderbook": {"spread_bps": 30}},
                "signal": Signal(direction="yes", p_true=0.70, conviction="high",
                                 rationale="flow")}


def _permissive_config():
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(edge_floor=0.02, min_liquidity_usdc=0.0, max_spread_bps=1e9,
               min_annualized_edge=0.0)
    return cfg


# ----- the loop -------------------------------------------------------------

def test_supervisor_runs_plan_in_order():
    sup = Supervisor([_Spy("a"), _Spy("b"), _Spy("c")],
                     SequentialRouter(["a", "b", "c"]))
    bb = sup.run("go")
    assert bb.notes == ["a", "b", "c"]
    assert [r.agent for r in bb.trace] == ["a", "b", "c"]


def test_halt_stops_the_loop_early():
    sup = Supervisor([_Spy("a"), _Spy("b", halt=True), _Spy("c")],
                     SequentialRouter(["a", "b", "c"]))
    bb = sup.run("go")
    assert bb.notes == ["a", "b"]            # c never ran
    assert bb.trace[-1].halt is True


def test_exception_in_subagent_halts_not_crashes():
    sup = Supervisor([_Spy("a"), _Spy("b", boom=True), _Spy("c")],
                     SequentialRouter(["a", "b", "c"]))
    bb = sup.run("go")
    assert bb.notes == ["a"]
    assert bb.trace[-1].agent == "b" and bb.trace[-1].ok is False
    assert "kaboom" in bb.trace[-1].summary


def test_unknown_agent_is_reported_and_halts():
    sup = Supervisor([_Spy("a")], SequentialRouter(["a", "ghost"]))
    bb = sup.run("go")
    assert bb.trace[-1].ok is False and "unknown" in bb.trace[-1].summary


def test_max_iters_bounds_the_loop():
    # a router that never stops would loop forever without the bound
    router = CallbackRouter(lambda bb, agents: "a")
    sup = Supervisor([_Spy("a")], router, max_iters=4)
    bb = sup.run("go")
    assert len(bb.trace) == 4


def test_on_event_streams_loop_events():
    events = []
    sup = Supervisor([_Spy("a"), _Spy("b")], SequentialRouter(["a", "b"]),
                     on_event=events.append)
    sup.run("go")
    types = [e["type"] for e in events]
    assert types[0] == "run_start" and types[-1] == "run_end"
    assert types.count("agent_start") == 2 and types.count("agent_result") == 2


# ----- RiskAgent (real decision math) ---------------------------------------

def test_risk_agent_sizes_a_buy_from_signal():
    cfg = _permissive_config()
    bb = Blackboard(goal="size", config=cfg,
                    signal={"direction": "yes", "p_true": 0.70, "conviction": "high",
                            "market_price": 0.40},
                    data={"liquidity": 50_000.0, "spread_bps": 30, "days_to_expiry": 10.0})
    res = RiskAgent(cfg).run(bb)
    assert res.ok and bb.risk["action"] == "buy"
    assert bb.risk["size_usdc"] > 0 and bb.risk["edge"] > 0


def test_risk_agent_holds_and_halts_with_no_lean():
    cfg = _permissive_config()
    bb = Blackboard(goal="size", config=cfg,
                    signal={"direction": "none", "p_true": 0.50, "market_price": 0.50},
                    data={"liquidity": 50_000.0, "days_to_expiry": 10.0})
    res = RiskAgent(cfg).run(bb)
    assert bb.risk["action"] == "hold" and res.halt is True


def test_risk_agent_fails_without_a_signal():
    res = RiskAgent(_permissive_config()).run(Blackboard(goal="x"))
    assert res.ok is False and res.halt is True


# ----- factory + end-to-end with a fake graph -------------------------------

def test_build_supervisor_assembles_strategy_agents():
    sup = build_supervisor(graph=_FakeGraph(), config=_permissive_config(), strategy="full")
    assert list(sup.agents) == ["data", "signal", "risk"]


def test_run_strategy_full_pipeline_offline():
    bb = run_strategy(_FakeMarket(), graph=_FakeGraph(), config=_permissive_config(),
                      strategy="full")
    assert [r.agent for r in bb.trace] == ["data", "signal", "risk"]
    assert bb.signal["p_true"] == 0.70
    assert bb.risk["action"] == "buy"
    assert "strategy run" in bb.summary()


# ----- LLM router (scripted fake model) -------------------------------------

class _FakeLLM:
    """Returns a scripted next-agent choice per invoke()."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def invoke(self, messages):
        from types import SimpleNamespace
        choice = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return SimpleNamespace(content=f'{{"next": "{choice}", "why": "test"}}')


def test_llm_router_drives_the_loop():
    llm = _FakeLLM(["data", "signal", "risk", "stop"])
    bb = run_strategy(_FakeMarket(), graph=_FakeGraph(), config=_permissive_config(),
                      router=LLMRouter(llm))
    assert [r.agent for r in bb.trace] == ["data", "signal", "risk"]
    assert bb.risk["action"] == "buy"


def test_llm_router_stops_on_unparseable_choice():
    class _Bad:
        def invoke(self, m):
            from types import SimpleNamespace
            return SimpleNamespace(content="I think maybe we should... hmm")
    bb = run_strategy(_FakeMarket(), graph=_FakeGraph(), config=_permissive_config(),
                      router=LLMRouter(_Bad()))
    assert bb.trace == []                     # nothing ran; safe stop


def test_llm_router_loop_guard_prevents_repeats():
    # model fixates on "data"; max_repeat=1 means it runs once then stops
    bb = run_strategy(_FakeMarket(), graph=_FakeGraph(), config=_permissive_config(),
                      router=LLMRouter(_FakeLLM(["data", "data", "data"])))
    assert [r.agent for r in bb.trace] == ["data"]
