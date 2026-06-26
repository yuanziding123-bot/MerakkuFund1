"""Contract tests for Lab mode permission policy."""
from __future__ import annotations

import pytest


def test_lab_mode_allows_research_tools_and_blocks_live_tools():
    from polyagents.runtime.session import PermissionPolicy

    policy = PermissionPolicy.for_mode("lab")

    assert policy.allows("create_hypothesis")
    assert policy.allows("run_backtest")
    assert policy.allows("write_evaluation")
    assert not policy.allows("submit_order")
    assert not policy.allows("halt")


def test_permission_denial_is_structured_and_audited():
    from polyagents.runtime.session import AgentSession, PermissionDenied

    session = AgentSession(mode="lab", tenant="default")

    with pytest.raises(PermissionDenied) as exc:
        session.call_tool("submit_order", {"market_token_id": "token_yes"})

    assert exc.value.code == "permission_denied"
    assert session.audit.events[-1]["event_type"] == "permission.denied"
