"""DB foundation — aihf_ table registry, engine URL coercion, shared-engine
read/write (the point of moving to a cloud DB: everyone sees the same data)."""
from __future__ import annotations

from sqlalchemy import inspect

from polyagents.objects import make
from polyagents.storage.audit_store import AuditStore
from polyagents.storage.engine import coerce_url, make_engine
from polyagents.storage.objects_store import ObjectStore
from polyagents.storage.tables import TABLE_PREFIX, audit_events, objects, table_name


def test_table_names_use_aihf_prefix():
    assert TABLE_PREFIX == "aihf_"
    assert objects.name == "aihf_objects"
    assert audit_events.name == "aihf_audit_events"
    assert table_name("forecasts") == "aihf_forecasts"


def test_coerce_url_maps_args():
    assert coerce_url(":memory:") == "sqlite://"
    assert coerce_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"
    assert coerce_url("/tmp/x.db").startswith("sqlite:///")


def test_created_tables_carry_the_prefix():
    eng = make_engine("sqlite://")
    ObjectStore(engine=eng); AuditStore(engine=eng)
    names = set(inspect(eng).get_table_names())
    assert {"aihf_objects", "aihf_promotion_events", "aihf_audit_events"} <= names


def test_stores_on_one_engine_share_data():
    # the cloud goal: separate store instances (≈ separate web requests/users) on
    # the same engine read each other's writes.
    eng = make_engine("sqlite://")
    writer, reader = ObjectStore(engine=eng), ObjectStore(engine=eng)
    h = writer.save(make("hypothesis", snapshot_id="s1", statement="crypto news edge"))
    assert reader.get(h.id) == h                       # reader sees writer's row
    assert reader.counts() == {"hypothesis": 1}

    audit = AuditStore(engine=eng)
    audit.log("sess1", "tool.call", {"name": "scan_markets"}, mode="ask")
    assert AuditStore(engine=eng).count() == 1         # another instance sees it
