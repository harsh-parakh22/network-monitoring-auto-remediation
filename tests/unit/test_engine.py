"""Engine tests with fake probes: full failure -> classification journeys."""

from __future__ import annotations

from netmon.config.models import MonitoringConfig, ProbeType, TargetConfig
from netmon.engine import TargetRunner
from netmon.health.classifier import FailureClass
from netmon.probes.base import Probe, ProbeResult, ProbeStatus


class FakeProbe(Probe):
    def __init__(self, probe_type: ProbeType, outcomes: list[bool]) -> None:
        super().__init__(TargetConfig(name="t1", host="h", checks=[probe_type]))
        self.outcomes = list(outcomes)
        self.probe_type = probe_type

    async def check(self) -> ProbeResult:
        ok = self.outcomes.pop(0) if self.outcomes else False
        return ProbeResult(
            target=self.target.name,
            probe=self.probe_type,
            status=ProbeStatus.OK if ok else ProbeStatus.FAILED,
            latency_ms=1.0 if ok else None,
            error_class=None if ok else "refused",
        )


def make_runner(outcomes: dict[ProbeType, list[bool]], captured: list) -> TargetRunner:
    cfg = MonitoringConfig()
    target = TargetConfig(name="t1", host="h", checks=list(outcomes.keys()))
    probes = [FakeProbe(pt, seq) for pt, seq in outcomes.items()]

    def on_failure(target_cfg, failure_class):
        captured.append(failure_class)

    return TargetRunner(cfg, target, probes=probes, on_failure_classified=on_failure)


async def test_healthy_target_produces_no_incident():
    captured: list = []
    runner = make_runner({ProbeType.TCP: [True, True, True]}, captured)
    for _ in range(3):
        await runner.run_once()
    assert not runner.incident_open
    assert captured == []


async def test_persistent_tcp_failure_classifies_service_down():
    captured: list = []
    runner = make_runner({ProbeType.TCP: [False] * 3}, captured)
    for _ in range(3):
        await runner.run_once()
    assert runner.incident_open
    assert captured[-1] is FailureClass.SERVICE_DOWN


async def test_transient_failure_does_not_trigger():
    captured: list = []
    runner = make_runner({ProbeType.TCP: [True, False, True, False, True]}, captured)
    for _ in range(5):
        await runner.run_once()
    assert not runner.incident_open
    assert captured == []


async def test_icmp_and_tcp_down_is_host_unreachable():
    captured: list = []
    runner = make_runner({ProbeType.ICMP: [False] * 3, ProbeType.TCP: [False] * 3}, captured)
    for _ in range(3):
        await runner.run_once()
    assert captured[-1] is FailureClass.HOST_UNREACHABLE


async def test_tcp_up_http_down_is_app_unhealthy():
    captured: list = []
    runner = make_runner(
        {ProbeType.TCP: [True] * 3, ProbeType.HTTP: [False] * 3}, captured
    )
    for _ in range(3):
        await runner.run_once()
    assert captured[-1] is FailureClass.APP_UNHEALTHY


async def test_recovery_after_failure_closes_incident():
    captured: list = []
    runner = make_runner({ProbeType.TCP: [False, False, False, True, True]}, captured)
    for _ in range(5):
        await runner.run_once()
    assert runner.incident_open  # stays open until remediation layer closes it
    # metrics should show healthy state now
    from netmon.metrics import registry

    value = registry.TARGET_HEALTH.labels(target="t1", probe="tcp")._value.get()
    assert value == 0.0  # healthy
