"""Incident store tests: lifecycle, audit, and MTTR math."""

from __future__ import annotations

import time

from netmon.storage.store import IncidentStore


def test_incident_lifecycle():
    store = IncidentStore()
    iid = store.open_incident("app-01", "service_down")
    assert len(store.open_incidents()) == 1
    assert store.open_incidents()[0].target == "app-01"

    store.resolve_incident(iid, remediated=True)
    assert store.open_incidents() == []
    resolved = store.recent_incidents()[0]
    assert resolved.remediated
    assert resolved.resolved_at >= resolved.started_at


def test_mttr_across_incidents():
    store = IncidentStore()
    t0 = time.time()
    for offset, dur in ((0, 10.0), (100, 30.0)):
        iid = store.open_incident("app-01", "service_down")
        # resolve with a controlled duration by patching time via direct SQL
        store._conn.execute(
            "UPDATE incidents SET started_at = ?, resolved_at = ? WHERE id = ?",
            (t0 + offset, t0 + offset + dur, iid),
        )
        store._conn.commit()
    assert abs(store.mttr_seconds() - 20.0) < 0.001


def test_mttr_none_when_nothing_resolved():
    store = IncidentStore()
    store.open_incident("db-01", "host_unreachable")
    assert store.mttr_seconds() is None


def test_transition_and_remediation_audit_roundtrip():
    store = IncidentStore()
    store.record_transition("app-01", "tcp", "healthy", "degraded")
    store.record_remediation("app-01", "service_down", "restart_container", True, "recovery verified")
    history = store.remediation_history(target="app-01")
    assert len(history) == 1
    assert history[0]["policy"] == "service_down"
    assert history[0]["success"] == 1


def test_open_incidents_filter_by_target():
    store = IncidentStore()
    store.open_incident("app-01", "service_down")
    store.open_incident("db-01", "host_unreachable")
    assert len(store.open_incidents()) == 2
    assert len(store.open_incidents(target="db-01")) == 1
    assert store.open_incidents(target="db-01")[0].failure_class == "host_unreachable"
