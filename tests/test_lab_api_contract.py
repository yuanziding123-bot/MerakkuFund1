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
