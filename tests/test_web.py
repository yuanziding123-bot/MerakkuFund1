"""Tests for the web chat layer: tools, skills registry, endpoints (no LLM)."""
from __future__ import annotations


def test_build_tools_exposes_the_trading_surface():
    from polyagents.web.agent import build_tools

    names = {t.name for t in build_tools()}
    for expected in ("scan_markets", "market_snapshot", "size_position",
                     "paper_execute", "portfolio_status", "settle_markets",
                     "pnl_report", "evaluation_report",
                     "crypto_price", "crypto_24h", "crypto_klines",
                     "list_events", "recent_trades", "verify_trade_math"):
        assert expected in names


def test_skills_registry_lists_skill_folders():
    from polyagents.web.agent import list_skills

    skills = list_skills()
    ids = {s["id"] for s in skills}
    assert {"polymarket-trading", "market-research", "cross-market-arb"} <= ids
    pt = next(s for s in skills if s["id"] == "polymarket-trading")
    assert pt["name"] and pt["description"] and pt["body"]


def test_compose_prompt_selects_skills():
    from polyagents.web.agent import _compose_prompt

    only_trading = _compose_prompt(["polymarket-trading"])
    assert "p_true" in only_trading and "SKILL:" not in only_trading   # single skill, no header

    both = _compose_prompt(["polymarket-trading", "market-research"])
    assert "SKILL:" in both and "market research" in both.lower()

    # unknown selection falls back to all skills, never empty
    assert _compose_prompt(["nope"]).strip()


def test_mcp_registry_lists_servers_with_tools():
    from polyagents.web.agent import list_mcp_servers

    servers = list_mcp_servers()
    ids = {s["id"] for s in servers}
    assert {"polyagents", "crypto", "polydata", "compliance",
            "qlib-backtest", "polymarket-docs"} <= ids
    crypto = next(s for s in servers if s["id"] == "crypto")
    assert "crypto_price" in crypto["tools"] and crypto["in_chat"] is True
    polydata = next(s for s in servers if s["id"] == "polydata")
    assert polydata["in_chat"] is True
    docs = next(s for s in servers if s["id"] == "polymarket-docs")
    assert docs["in_chat"] is False


def test_server_app_has_routes():
    from polyagents.web.server import app

    paths = {r.path for r in app.routes}
    for p in ("/", "/api/chat", "/api/skills", "/api/mcp", "/api/portfolio",
              "/api/markets", "/api/backtest", "/api/lab/hypotheses",
              "/api/lab/hypotheses/{id}", "/api/lab/hypotheses/{id}/backtests",
              "/api/lab/reports/{id}", "/api/lab/data/status",
              "/api/lab/data/ingest", "/api/lab/monitor/opportunities"):
        assert p in paths


def test_lab_ui_uses_lab_api_contract():
    from pathlib import Path

    html = Path("polyagents/web/static/index.html").read_text()

    assert "function showLabCreate" in html
    assert "function submitLabHypothesis" in html
    assert "prompt(" not in html
    assert "m.beats_market || m.brier_delta>0" in html
    assert "review report" in html and "openLabReport" in html
    assert "本地已结算价格历史" in html
    assert "log_loss_model" in html
    assert "pit_warnings" in html
    assert "signal_model" in html or "model=" in html
    assert "真实历史 collections" in html
    assert "样本量不足" in html
    assert "Data ingestion · historical settled collections" in html
    assert "Strategy-aware evidence backtest" in html
    assert "Dry-run monitor · active opportunities" in html
    assert "Reports · EvaluationReport ledger" in html
    assert "Historical replay · resolved price history" in html
    assert "qlib factor backtest · experimental" in html
    assert "Launch readiness · system checks" in html
    assert "function loadLabSystemStatus" in html
    assert "labFetchJson('/api/lab/system/status')" in html
    assert "点击任一 report 查看完整 EvaluationReport review" in html
    assert "Open Backtest workflow" in html
    assert "auto-detected during backtest" in html
    assert "auto-detected from collections" in html
    assert "labNewFeatures" not in html
    assert "确认数据" in html
    assert "运行 evidence" in html
    assert "查看 report" in html
    assert "Supported Lab strategies" in html
    assert "下一步：点 Run evidence" in html
    assert "请先在 Market 下拉里选择一个有效市场" in html
    for strategy_id in (
        "linear-factor-v1",
        "market-naive-v1",
        "momentum-v1",
        "flow-imbalance-v1",
        "microstructure-v1",
        "sentiment-v1",
        "contrarian-v1",
    ):
        assert strategy_id in html
    assert "labFetchJson('/api/lab/data/status')" in html
    assert "labFetchJson('/api/lab/data/ingest-jobs'" in html
    assert "'/api/lab/data/ingest-jobs/'+encodeURIComponent(job.id)" in html
    assert "news_items_skipped_future" in html
    assert "skipped_undated" in html
    assert "news_evidence" in html
    assert "sample_structure" in html
    assert "condition clusters" in html
    assert "Review order: source" in html
    assert "rerun ingestion/backtest to populate PIT news evidence" in html
    assert "labFetchJson('/api/lab/monitor/opportunities'" in html
    assert "dry_run=true" in html
    assert "No opportunity means the selected strategy" in html
    assert "function labFetchJson" in html
    assert "labFetchJson('/api/lab/hypotheses')" in html
    assert "'/api/lab/hypotheses/'+encodeURIComponent(id)+'/backtests'" in html
    assert "labFetchJson('/api/lab/reports/'+encodeURIComponent(r.report_id)" in html
    assert "function renderLabReport" in html
    assert "function runAlphaTest" not in html


def test_backtest_python_falls_back_to_current_interpreter(monkeypatch):
    import sys

    from polyagents.web import server

    monkeypatch.setitem(server.DEFAULT_CONFIG, "qlib_python", "C:\\qlib\\.venv\\Scripts\\python.exe")

    assert server._qlib_python() == sys.executable


def test_strategy_missing_api_key_error_is_actionable():
    from pathlib import Path

    server = Path("polyagents/web/server.py").read_text()

    assert "Strategy signal/full/trade runs need ANTHROPIC_API_KEY" in server
    assert "Strategy=research" in server
