"""End-to-end orchestration test: failure -> incident -> remediation ->
verified recovery -> incident closed, all through the real Orchestrator
with fake probes and a fake action (no Docker, no network)."""

from __future__ import annotations

import asyncio

from netmon.config.models import AppConfig
from netmon.orchestrator import Orchestrator
from netmon.remediation.actions import ActionRegistry, ActionResult
from netmon.remediation.engine import RemediationEngine
from netmon.storage.store import IncidentStore


class ScriptedProbe:
    """Plays back scripted success/failure outcomes."""

    probe_type_value = "tcp"

    def __init__(self, target_name: str, outcomes: list[bool]) -> None:
        self.outcomes = list(outcomes)
        self.target_name = target_name

    @property
    def probe_type(self):
        from netmon.probes.base import ProbeType

        return ProbeType.TCP

    async def check(self):
        from netmon.probes.base import ProbeResult, ProbeStatus

        ok = self.outcomes.pop(0) if self.outcomes else True
        return ProbeResult(
            target=self.target_name,
            probe=self.probe_type,
            status=ProbeStatus.OK if ok else ProbeStatus.FAILED,
            latency_ms=1.0 if ok else None,
            error_class=None if ok else "refused",
        )


async def test_full_incident_lifecycle_with_remediation():
    # Script: 3 failures (trip the breaker... threshold), then healthy forever.
    outcomes = [False, False, False, True, True, True, True]
    probe = ScriptedProbe("app-01", outcomes)

    def factory(target_cfg):
        return [probe]

    cfg = AppConfig(
        targets=[{"name": "app-01", "host": "h", "checks": ["tcp"], "interval_seconds": 0.01}],
        remediation={
            "policies": {
                "service_down": {
                    "action": "restart_container",
                    "max_attempts": 3,
                    "cooldown_seconds": 0,
                    "verify_interval_seconds": 0.01,
                    "verify_timeout_seconds": 1.0,
                }
            }
        },
    )

    store = IncidentStore()
    registry = ActionRegistry()
    actions_called: list[str] = []

    async def fake_restart(target: str) -> ActionResult:
        actions_called.append(target)
        return ActionResult(success=True, detail="restarted (fake)")

    registry.register("restart_container", fake_restart)
    remediation = RemediationEngine(cfg.remediation, registry=registry)
    orch = Orchestrator(cfg, store, remediation, probe_factory=factory)

    engine_task = asyncio.create_task(orch.engine.run())
    # Give the loop time to run through the scripted outcomes.
    await asyncio.sleep(0.3)
    orch.stop()
    await engine_task

    # Remediation ran once.
    assert actions_called == ["app-01"]
    # Audit trail has the attempt with recovery verified.
    assert remediation.audit[0].success
    # Store shows one resolved, remediated incident.
    incidents = store.recent_incidents()
    assert len(incidents) == 1
    assert incidents[0].remediated is True
    assert incidents[0].resolved_at is not None
    # MTTR is measurable.
    assert store.mttr_seconds(target="app-01") is not None
    # No incidents left open.
    assert store.open_incidents() == []
