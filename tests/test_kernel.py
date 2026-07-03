"""Agent-loop kernel — goal-directed planning picks the minimal path."""
from __future__ import annotations

from polyagents.kernel.capabilities import demo_registry
from polyagents.kernel.core import AgentLoop, Capability, Goal, next_capability
from polyagents.kernel.intent import recognize


def _cap(name, pre, eff, cost=1):
    return Capability(name, name, frozenset(pre), frozenset(eff),
                      lambda ctx, eff=eff: {k: True for k in eff}, cost)


# ----- the headline: backtest goal -> data -> backtest, nothing else --------

def test_backtest_goal_runs_only_data_and_backtest():
    goal = recognize("拉事件 X 的历史数据,我要做 backtest", event="X")
    ctx = AgentLoop(demo_registry()).run(goal)
    path = [s.capability for s in ctx.trace]
    assert path == ["data_agent", "backtest_agent"]      # minimal path
    assert "signal_agent" not in path and "risk_agent" not in path
    assert ctx.done() and "backtest_report" in ctx.facts


def test_recognize_maps_requests_to_goals():
    assert recognize("backtest this event").targets == frozenset({"backtest_report"})
    assert recognize("评估我们最近跑赢市场了吗").targets == frozenset({"evaluation"})
    assert recognize("解释一下什么是校准").targets == frozenset({"answer"})


# ----- planner correctness --------------------------------------------------

def test_planner_chains_preconditions():
    reg = [_cap("A", {"x"}, {"y"}), _cap("B", {"y"}, {"z"}), _cap("C", {"x"}, {"w"})]
    ctx = AgentLoop(reg).run(Goal(frozenset({"z"}), {"x": 1}))
    assert [s.capability for s in ctx.trace] == ["A", "B"]   # C is irrelevant -> skipped


def test_planner_picks_by_goal_so_new_capability_is_auto_used():
    # same registry, different goal -> the previously-unused capability runs
    reg = [_cap("A", {"x"}, {"y"}), _cap("B", {"y"}, {"z"}), _cap("C", {"x"}, {"w"})]
    ctx = AgentLoop(reg).run(Goal(frozenset({"w"}), {"x": 1}))
    assert [s.capability for s in ctx.trace] == ["C"]


def test_goal_already_satisfied_runs_nothing():
    ctx = AgentLoop(demo_registry()).run(Goal(frozenset({"event"}), {"event": "X"}))
    assert ctx.trace == [] and ctx.done()


def test_stuck_when_no_provider_for_a_precondition():
    reg = [_cap("B", {"y"}, {"z"})]                 # nothing produces 'y'
    ctx = AgentLoop(reg).run(Goal(frozenset({"z"}), {}))
    assert ctx.trace == [] and not ctx.done()
    assert next_capability(ctx, reg) is None


def test_max_steps_bounds_the_loop():
    c = _cap("loop", set(), {"noop"})
    ctx = AgentLoop([c], planner=lambda ctx, reg: c, max_steps=3).run(Goal(frozenset({"never"})))
    assert len(ctx.trace) == 3 and not ctx.done()


def test_capability_error_stops_with_a_failed_step():
    def boom(ctx):
        raise RuntimeError("kaboom")
    bad = Capability("bad", "", frozenset({"x"}), frozenset({"y"}), boom)
    ctx = AgentLoop([bad]).run(Goal(frozenset({"y"}), {"x": 1}))
    assert ctx.trace[-1].ok is False and "kaboom" in ctx.trace[-1].note


# ----- audit / events -------------------------------------------------------

def test_audit_and_events_are_emitted():
    events = []

    class _Audit:
        def __init__(self): self.rows = []
        def log(self, sid, et, payload, mode=""): self.rows.append((et, payload))

    audit = _Audit()
    AgentLoop(demo_registry(), audit=audit, on_event=events.append).run(
        recognize("backtest event X", event="X"))
    etypes = [e["type"] for e in events]
    assert etypes[0] == "loop.start" and etypes[-1] == "loop.end"
    assert any(e["type"] == "capability.done" and e["name"] == "data_agent" for e in events)
    assert any(et == "capability.ran" for et, _ in audit.rows)
