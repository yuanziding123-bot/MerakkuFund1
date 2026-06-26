"""Lab hypothesis service."""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

from polyagents.default_config import DEFAULT_CONFIG

from .repository import LabRepository
from .schemas import CreateHypothesisRequest, CreateHypothesisResponse, HypothesisRecord, utc_now


def _short_hash(raw: str) -> str:
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


@lru_cache(maxsize=1)
def default_repository() -> LabRepository:
    base = Path(
        os.getenv(
            "POLYAGENTS_LAB_DB",
            str(Path(DEFAULT_CONFIG["project_root"]) / ".polyagents" / "cache" / "lab.db"),
        )
    )
    return LabRepository(base)


def create_hypothesis(
    request: CreateHypothesisRequest,
    *,
    repo: LabRepository | None = None,
) -> CreateHypothesisResponse:
    repo = repo or default_repository()
    now = utc_now()
    suffix = _short_hash(f"{request.statement}|{request.category_filter}|{now}")
    hyp_id = f"hyp_{suffix}"
    snapshot_id = f"snap_{_short_hash(hyp_id + now)}"
    record = HypothesisRecord(
        id=hyp_id,
        statement=request.statement,
        category_filter=request.category_filter,
        feature_set=list(request.feature_set),
        prompt_version=request.prompt_version,
        model_version=request.model_version,
        snapshot_id=snapshot_id,
        lineage=dict(request.lineage),
        created_at=now,
        updated_at=now,
    )
    repo.save_hypothesis(record)
    return CreateHypothesisResponse(id=hyp_id, state=record.state, version=record.version, snapshot_id=snapshot_id)


def list_hypotheses(*, repo: LabRepository | None = None) -> list[dict]:
    repo = repo or default_repository()
    return repo.list_hypotheses()


def get_hypothesis(hypothesis_id: str, *, repo: LabRepository | None = None) -> HypothesisRecord | None:
    repo = repo or default_repository()
    return repo.get_hypothesis(hypothesis_id)
