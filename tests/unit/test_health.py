"""Tests for the health state machine and failure classifier.

These tests drive the machine through full failure/recovery journeys
using real ProbeResult objects, asserting on every transition.
"""

from __future__ import annotations

import pytest

from netmon.config.models import MonitoringConfig
from netmon.health.classifier import FailureClass, FailureClassifier
from netmon.health.state_machine import HealthState, HealthStateMachine
from netmon.probes.base import ProbeResult, ProbeStatus, ProbeType


def make_config(**kw) -> MonitoringConfig:
    defaults = dict(failure_threshold=3, degraded_threshold=1, recovery_threshold=2)
    defaults.update(kw)
    return MonitoringConfig(**defaults)


def ok(probe=ProbeType.TCP) -> ProbeResult:
    return ProbeResult(target="t", probe=probe, status=ProbeStatus.OK, latency_ms=1.0)


def fail(probe=ProbeType.TCP, error_class="refused") -> ProbeResult:
    return ProbeResult(target="t", probe=probe, status=ProbeStatus.FAILED, error="x", error_class=error_class)


# ------------------------------------------------------ state machine


def test_healthy_until_failure_threshold():
    m = HealthStateMachine(make_config())
    assert m.state is HealthState.HEALTHY
    assert m.record(fail()) is not None  # HEALTHY -> DEGRADED
    assert m.record(fail()) is None      # still DEGRADED
    t = m.record(fail())                 # 3rd failure -> UNHEALTHY
    assert t is not None
    assert t.from_state is HealthState.DEGRADED
    assert t.to_state is HealthState.UNHEALTHY


def test_degraded_recovers_on_single_success():
    m = HealthStateMachine(make_config())
    m.record(fail())
    assert m.record(ok()) is not None  # DEGRADED -> HEALTHY
    assert m.state is HealthState.HEALTHY


def test_recovery_requires_hysteresis():
    m = HealthStateMachine(make_config())
    for _ in range(3):
        m.record(fail())
    assert m.state is HealthState.UNHEALTHY
    assert m.record(ok()) is not None   # UNHEALTHY -> RECOVERING
    assert m.state is HealthState.RECOVERING
    assert m.record(ok()) is not None   # RECOVERING -> HEALTHY (2nd success)
    assert m.state is HealthState.HEALTHY


def test_failure_during_recovery_drops_back_to_unhealthy():
    m = HealthStateMachine(make_config())
    for _ in range(3):
        m.record(fail())
    m.record(ok())
    assert m.state is HealthState.RECOVERING
    m.record(fail())
    assert m.state is HealthState.UNHEALTHY


def test_flapping_does_not_toggle_alerting_state():
    """A target alternating pass/fail never gets past DEGRADED — this is
    why hysteresis exists: no alert storm, no remediation storm."""
    m = HealthStateMachine(make_config())
    for _ in range(10):
        m.record(fail())
        m.record(ok())
        assert m.state in (HealthState.DEGRADED, HealthState.HEALTHY)
        assert m.state is not HealthState.UNHEALTHY


def test_error_class_tracked():
    m = HealthStateMachine(make_config())
    m.record(fail(error_class="timeout"))
    assert m.last_error_class == "timeout"


# ------------------------------------------------------ classifier


def build_classifier(icmp: HealthState | None, tcp: HealthState | None, http: HealthState | None,
                     tcp_error: str | None = None) -> FailureClassifier:
    c = FailureClassifier()
    for probe, state in (("icmp", icmp), ("tcp", tcp), ("http", http)):
        if state is None:
            continue
        m = HealthStateMachine(make_config())
        m.state = state
        m.last_error_class = tcp_error
        c.register(f"t1:{probe}", m)
    return c


@pytest.mark.parametrize(
    "icmp,tcp,http,tcp_error,expected",
    [
        (HealthState.UNHEALTHY, HealthState.UNHEALTHY, HealthState.UNHEALTHY, "timeout", FailureClass.HOST_UNREACHABLE),
        (HealthState.HEALTHY, HealthState.UNHEALTHY, HealthState.UNHEALTHY, "refused", FailureClass.SERVICE_DOWN),
        (HealthState.HEALTHY, HealthState.UNHEALTHY, HealthState.UNHEALTHY, "timeout", FailureClass.PORT_FILTERED),
        (HealthState.HEALTHY, HealthState.HEALTHY, HealthState.UNHEALTHY, None, FailureClass.APP_UNHEALTHY),
        (None, HealthState.UNHEALTHY, None, "refused", FailureClass.SERVICE_DOWN),
    ],
)
def test_classification_matrix(icmp, tcp, http, tcp_error, expected):
    c = build_classifier(icmp, tcp, http, tcp_error)
    assert c.classify("t1") is expected


def test_all_healthy_classifies_unknown():
    c = build_classifier(HealthState.HEALTHY, HealthState.HEALTHY, HealthState.HEALTHY)
    assert c.classify("t1") is FailureClass.UNKNOWN


def test_unknown_target_classifies_unknown():
    c = FailureClassifier()
    assert c.classify("nope") is FailureClass.UNKNOWN
