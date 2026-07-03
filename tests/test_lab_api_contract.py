"""Contract tests for the future Lab HTTP API.

These lock the endpoint surface agreed in
docs/product/lab-module-api-data-contract.md.
"""
from __future__ import annotations


def test_lab_routes_are_registered():
    from polyagents.web.server import app

    paths = {route.path for route in app.routes}

    assert "/api/lab/hypotheses" in paths
    assert "/api/lab/hypotheses/{id}" in paths
    assert "/api/lab/hypotheses/{id}/backtests" in paths
    assert "/api/lab/backtests/{id}" in paths
    assert "/api/lab/reports/{id}" in paths
    assert "/api/lab/system/status" in paths


def test_lab_hypothesis_create_response_shape(tmp_path):
    from polyagents.lab.repository import LabRepository
    from polyagents.lab.schemas import CreateHypothesisRequest
    from polyagents.lab.service import create_hypothesis

    repo = LabRepository(tmp_path / "lab.db")
    request = CreateHypothesisRequest(
        statement="Crypto news markets update slower than the model",
        category_filter="crypto",
        feature_set=["news_sentiment", "similar_markets"],
        prompt_version="signal-v1",
        model_version="claude-sonnet-4",
        lineage={"source": "manual", "parents": []},
    )

    response = create_hypothesis(request, repo=repo)

    assert response.id.startswith("hyp_")
    assert response.state == "draft"
    assert response.version == 1
    assert response.snapshot_id.startswith("snap_")
    repo.close()


def test_lab_http_create_backtest_report_flow(tmp_path, monkeypatch):
    import polyagents.lab.service as service
    import polyagents.web.server as server
    from fastapi.testclient import TestClient

    monkeypatch.setenv("POLYAGENTS_LAB_DB", str(tmp_path / "lab.db"))
    monkeypatch.setitem(server.DEFAULT_CONFIG, "db_path", str(tmp_path / "data.db"))
    service.default_repository.cache_clear()
    server.default_repository.cache_clear()
    client = TestClient(server.app)

    created = client.post(
        "/api/lab/hypotheses",
        json={
            "statement": "Crypto news markets update slower than the model",
            "category_filter": "crypto",
            "feature_set": ["news_sentiment", "similar_markets"],
            "prompt_version": "signal-v1",
            "model_version": "claude-sonnet-4",
            "lineage": {"source": "manual:test", "parents": []},
        },
    )
    assert created.status_code == 200
    hypothesis_id = created.json()["id"]

    detail = client.get(f"/api/lab/hypotheses/{hypothesis_id}")
    assert detail.status_code == 200
    assert detail.json()["hypothesis"]["statement"].startswith("Crypto news")
    assert detail.json()["reports"] == []

    run = client.post(
        f"/api/lab/hypotheses/{hypothesis_id}/backtests",
        json={
            "time_window": {
                "start": "2026-03-01T00:00:00Z",
                "end": "2026-06-01T00:00:00Z",
            },
            "market_filter": {"category": "crypto", "settled_only": True},
            "model_version": "claude-sonnet-4",
            "prompt_version": "signal-v1",
            "calibrator_id": "shrink-to-market-v1",
            "pit_strict": True,
            "max_markets": 100,
        },
    )
    assert run.status_code == 200
    report_id = run.json()["report_id"]

    report = client.get(f"/api/lab/reports/{report_id}")
    assert report.status_code == 200
    body = report.json()
    assert body["type"] == "evaluation_report"
    assert body["hypothesis_id"] == hypothesis_id
    assert body["time_window"]["end"] == "2026-06-01T00:00:00Z"
    assert body["backtest_config"]["max_markets"] == 100
    assert body["market_universe"]["source"] in {"collections", "fixture"}
    assert body["data_quality"]["uses_fixture_data"] is True
    assert "data_quality" in body
    assert "scorecard" in body
    assert body["metrics"]["n"] == len(body["market_sample"])

    detail = client.get(f"/api/lab/hypotheses/{hypothesis_id}").json()
    assert detail["reports"][0]["id"] == report_id
