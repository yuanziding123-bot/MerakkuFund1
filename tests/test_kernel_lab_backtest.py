"""lab-backtest pack — backfill_outcomes + lab_backtest (selectable, not core).

Label stored snapshots with realised outcomes, then run a Lab feature-strategy over
them via the colleague's BacktestRunner.run evidence path. Driven by a scripted LLM;
the workers are faked (no store / network)."""
from __future__ import annotations

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import (backfill_outcomes_capability,
                                            lab_backtest_capability)
from polyagents.kernel.packs import CORE, PACKS, kernel_capability_names


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_lab_backtest_pack_is_selectable_not_core():
    assert "lab-backtest" in PACKS
    assert PACKS["lab-backtest"]["capabilities"] == ["backfill_outcomes", "lab_backtest"]
    for cap in ("backfill_outcomes", "lab_backtest"):
        assert cap not in CORE                                  # not always-on
        assert cap not in kernel_capability_names([])           # absent with no packs
        assert cap in kernel_capability_names(["lab-backtest"]) # present when selected


def test_backfill_outcomes_labels_snapshots():
    def fn(query):
        return {"query": query, "backend": "postgres", "scanned": 225,
                "already_labeled": 0, "newly_labeled": 18, "still_unresolved": 207,
                "labeled_total": 18, "store_counts": {"candles": 17145, "collections": 225}}

    llm = FakeLLM('{"action":"call","capability":"backfill_outcomes"}',
                  '{"action":"final","answer":"标注了 18 条"}')
    res = KernelController([backfill_outcomes_capability(fn)], llm).run("回填结算结果")
    assert [s.capability for s in res.trace] == ["backfill_outcomes"]
    b = res.facts["outcome_backfill"]
    assert b["newly_labeled"] == 18 and b["backend"] == "postgres"


def test_lab_backtest_reports_metrics_and_gates():
    def fn(query):
        return {"query": query, "strategy_id": "linear-factor-v1", "category": "crypto",
                "n": 18, "uses_fixture": False, "brier_delta": 0.0023, "ece": 0.0226,
                "gates": {"beats_market": True, "ece_pass": True, "paper_ready": False},
                "report_id": "eval_abc"}

    llm = FakeLLM('{"action":"call","capability":"lab_backtest"}',
                  '{"action":"final","answer":"回测完成"}')
    res = KernelController([lab_backtest_capability(fn)], llm).run("用 linear-factor 策略跑 Lab 回测")
    assert [s.capability for s in res.trace] == ["lab_backtest"]
    r = res.facts["lab_backtest"]
    assert r["uses_fixture"] is False and r["n"] == 18
    assert r["gates"]["paper_ready"] is False
