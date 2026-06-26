"""The object state machine — v0.2's spine. Pure, no LLM, no network."""
from __future__ import annotations

import pytest

from polyagents.objects import (
    ALLOWED_TRANSITIONS, EvalSummary, Hypothesis, IllegalTransition, Lineage,
    eval_gate_passed, make, promote, revise, risk_gate_passed,
)


def _edge(delta_ci, *, n=40, ece=0.03):
    """An EvalSummary whose beats_market reflects the CI upper bound < 0."""
    lo, hi = delta_ci
    return EvalSummary(n=n, brier_model=0.15, brier_market=0.18,
                       brier_delta=(lo + hi) / 2, brier_delta_ci=delta_ci,
                       ece=ece, beats_market=hi < 0, sample_adequate=n >= 30)


def test_make_starts_at_version_1_with_empty_lineage():
    h = make("hypothesis", snapshot_id="snap_a18f3c", statement="crypto news freshness",
             category_filter="crypto")
    assert isinstance(h, Hypothesis)
    assert h.version == 1 and h.state == "draft"
    assert h.id.startswith("H-") and h.snapshot_id == "snap_a18f3c"
    assert h.lineage == Lineage(parent_id=None)
    assert h.statement == "crypto news freshness"


def test_promote_is_versioned_single_directional_and_evidenced():
    h = make("hypothesis", snapshot_id="s1")
    lab = promote(h, "lab", promoted_by="user:alice", evidence_ref="sess_0622")

    assert lab.state == "lab" and lab.version == 2
    assert h.state == "draft"                       # original is untouched (frozen)
    assert lab.id == h.id                           # same object, new version
    (ev,) = lab.lineage.events
    assert ev.from_state == "draft" and ev.to_state == "lab"
    assert ev.promoted_by == "user:alice" and ev.evidence_ref == "sess_0622"
    assert ev.promoted_at                            # stamped


def test_illegal_transitions_are_rejected():
    h = make("hypothesis", snapshot_id="s1")         # draft
    with pytest.raises(IllegalTransition):
        promote(h, "live", promoted_by="user:x")     # draft ↛ live (must pass gates)
    with pytest.raises(IllegalTransition):
        promote(h, "draft", promoted_by="user:x")    # no backward edges


def test_archived_is_terminal():
    assert ALLOWED_TRANSITIONS["archived"] == set()
    h = make("hypothesis", snapshot_id="s1")
    dead = promote(h, "archived", promoted_by="policy:kill")
    with pytest.raises(IllegalTransition):
        promote(dead, "lab", promoted_by="user:x")


def test_revise_bumps_version_keeps_state():
    h = make("hypothesis", snapshot_id="s1", prompt_version="v0.7.1")
    h2 = revise(h, prompt_version="v0.7.2")
    assert h2.version == 2 and h2.state == "draft" and h2.id == h.id
    assert h2.prompt_version == "v0.7.2"


def test_eval_gate_needs_real_edge_sample_and_calibration():
    # CI excludes 0 on the good side, enough samples, ECE in tolerance -> pass
    assert eval_gate_passed(_edge((-0.061, -0.013), n=40, ece=0.038))
    # CI includes 0 -> edge not proven
    assert not eval_gate_passed(_edge((-0.05, 0.01)))
    # too few samples
    assert not eval_gate_passed(_edge((-0.06, -0.02), n=28))
    # calibration out of tolerance
    assert not eval_gate_passed(_edge((-0.06, -0.02), ece=0.077))
    # nothing to judge
    assert not eval_gate_passed(None)


def test_risk_gate_requires_manual_approval():
    base = dict(paper_apy=0.184, max_drawdown=-0.038, paper_n=72)
    assert risk_gate_passed(**base, manual_approved=True)
    assert not risk_gate_passed(**base, manual_approved=False)   # never auto-opens
    assert not risk_gate_passed(paper_apy=0.05, max_drawdown=-0.038,
                                paper_n=72, manual_approved=True)  # apy below min
