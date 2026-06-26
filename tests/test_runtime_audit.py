"""Mode-scoped AgentSession (#4) + audit_events store (#3)."""
from __future__ import annotations

import pytest

from polyagents.runtime.session import AgentSession, PermissionPolicy
from polyagents.storage.audit_store import AuditStore


# ----- PermissionPolicy / AgentSession --------------------------------------

def test_ask_mode_is_readonly_cannot_trade():
    p = PermissionPolicy.for_mode("ask")
    assert p.readonly and not p.can_trade and p.can_promote


def test_live_mode_can_trade_lab_is_paper():
    assert PermissionPolicy.for_mode("live").can_trade is True
    lab = PermissionPolicy.for_mode("lab")
    assert lab.can_trade is False and lab.readonly is False


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        PermissionPolicy.for_mode("nope")


def test_session_exposes_readonly_and_unique_id():
    a, b = AgentSession("ask"), AgentSession("ask")
    assert a.readonly is True and a.id != b.id and a.id.startswith("s_")
    assert AgentSession("live").readonly is False


def test_session_logs_to_audit_with_session_id_and_mode():
    store = AuditStore(":memory:")
    s = AgentSession("ask", audit=store)
    s.log("session.start", model="claude-sonnet-4-6")
    s.log("tool.call", name="scan_markets")
    rows = store.recent()
    assert {r["event_type"] for r in rows} == {"session.start", "tool.call"}
    assert all(r["session_id"] == s.id and r["mode"] == "ask" for r in rows)
    start = next(r for r in rows if r["event_type"] == "session.start")
    assert start["payload"]["model"] == "claude-sonnet-4-6"


def test_session_without_audit_is_silent():
    AgentSession("ask").log("tool.call", name="x")     # no audit sink → no error


# ----- AuditStore ------------------------------------------------------------

def test_audit_recent_filters_by_session_and_orders_newest_first():
    store = AuditStore(":memory:")
    store.log("s1", "session.start", {})
    store.log("s2", "session.start", {})
    store.log("s1", "tool.call", {"name": "evaluate_forecast"})
    assert store.count() == 3
    s1 = store.recent(session_id="s1")
    assert [r["event_type"] for r in s1] == ["tool.call", "session.start"]   # DESC
    assert all(r["session_id"] == "s1" for r in s1)
