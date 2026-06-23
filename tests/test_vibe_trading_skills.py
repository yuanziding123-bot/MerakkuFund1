"""Regression tests for the Vibe-Trading-derived skill set."""
from __future__ import annotations

from pathlib import Path

from polyagents.web.agent import list_skills


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SKILLS = {
    "market-data",
    "backtest",
    "execution-model",
    "risk-analysis",
    "report-generation",
    "memory",
}

EXPECTED_TOOL_HINTS = {
    "market-data": ("scan_markets", "market_snapshot", "Market -> Hypothesis"),
    "backtest": ("evaluation_report", "EvaluationReport", "promotionRecommendation"),
    "execution-model": ("size_position", "paper_execute"),
    "risk-analysis": ("portfolio_status", "pnl_report", "promote_to_paper"),
    "report-generation": ("Research only", "EvaluationReport"),
    "memory": ("settle_markets", "pnl_report", "object lineage"),
}


def test_vibe_trading_derived_skills_are_discoverable():
    skills = {skill["id"]: skill for skill in list_skills()}
    assert EXPECTED_SKILLS <= set(skills)

    for skill_id in EXPECTED_SKILLS:
        skill = skills[skill_id]
        assert skill["name"] == skill_id
        assert skill["description"]
        assert "Vibe-Trading" in skill["body"] or "Vibe" in skill["body"]


def test_vibe_trading_derived_skills_reference_framework_tools():
    skills = {skill["id"]: skill for skill in list_skills()}
    for skill_id, hints in EXPECTED_TOOL_HINTS.items():
        body = skills[skill_id]["body"]
        for hint in hints:
            assert hint in body


def test_migration_report_exists():
    report = ROOT / "docs" / "vibe_trading_skill_test_report.md"
    text = report.read_text(encoding="utf-8")
    for skill_id in EXPECTED_SKILLS:
        assert skill_id in text
    assert "AIHF v0.2" in text
    assert "pi.dev is not the core engine" in text
