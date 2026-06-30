"""Object persistence: serialization round-trip + the SQLite store + promotions."""
from __future__ import annotations

import pytest

from polyagents.objects import (
    EvalSummary, Hypothesis, IllegalTransition, from_dict, make, promote, to_dict,
)
from polyagents.storage.objects_store import ObjectStore


def _hyp():
    return make("hypothesis", snapshot_id="snap_a18f3c",
                statement="crypto news freshness", category_filter="crypto",
                feature_set=("news_event", "rag_similar"),
                prompt_version="v0.7.2", model_version="claude-sonnet-4-6")


def test_to_from_dict_roundtrip_preserves_object():
    h = _hyp()
    h2 = from_dict(to_dict(h))
    assert h2 == h
    assert isinstance(h2, Hypothesis) and h2.feature_set == ("news_event", "rag_similar")


def test_roundtrip_with_lineage_and_eval_summary():
    h = promote(_hyp(), "lab", promoted_by="user:alice", evidence_ref="sess_1")
    ev = EvalSummary(n=40, brier_model=0.15, brier_market=0.18, brier_delta=0.03,
                     brier_delta_ci=(0.01, 0.05), ece=0.03, beats_market=True,
                     sample_adequate=True)
    h = type(h)(**{**to_dict(h), "lineage": h.lineage, "eval_summary": ev})
    h2 = from_dict(to_dict(h))
    assert h2.eval_summary.brier_delta_ci == (0.01, 0.05)
    assert h2.lineage.events[0].promoted_by == "user:alice"


def test_store_save_get_list():
    store = ObjectStore(":memory:")
    h = store.save(_hyp())
    assert store.get(h.id) == h
    assert [o.id for o in store.list(type="hypothesis")] == [h.id]
    assert store.list(state="paper") == []
    assert store.counts() == {"hypothesis": 1}


def test_store_promote_persists_new_version_and_audits():
    store = ObjectStore(":memory:")
    h = store.save(_hyp())
    moved = store.promote(h.id, "lab", promoted_by="user:alice", evidence_ref="sess_1")
    assert moved.state == "lab" and moved.version == 2
    assert store.get(h.id).state == "lab"            # persisted
    events = store.promotions(h.id)
    assert len(events) == 1
    assert events[0]["from_state"] == "draft" and events[0]["to_state"] == "lab"
    assert events[0]["promoted_by"] == "user:alice"


def test_store_rejects_illegal_promotion():
    store = ObjectStore(":memory:")
    h = store.save(_hyp())
    with pytest.raises(IllegalTransition):
        store.promote(h.id, "live", promoted_by="user:x")   # draft ↛ live


def test_store_promote_unknown_id_raises():
    store = ObjectStore(":memory:")
    with pytest.raises(KeyError):
        store.promote("nope", "lab", promoted_by="user:x")
