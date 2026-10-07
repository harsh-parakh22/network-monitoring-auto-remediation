"""Orchestrator: glues monitoring, incidents, remediation and recovery.

Flow when a target is declared UNHEALTHY:
  1. open an incident in the store (audit trail starts)
  2. hand the failure to the remediation engine (policy, cooldown,
     circuit breaker all apply)
  3. the remediation engine's verifier re-runs the target's probes —
     recovery must be *observed*, not assumed
  4. recovery closes the incident, records MTTR, exports the metric

Recovery without remediation (transient fault, human fixed it) is caught
by the state machine; the orchestrator closes the incident then, too.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from netmon.config.models import AppConfig, TargetConfig
from netmon.engine import MonitorEngine
from netmon.health.classifier import FailureClass
from netmon.metrics import registry as metrics
from netmon.probes import build_probes
from netmon.probes.base import Probe
from netmon.remediation.engine import RemediationEngine
from netmon.storage.store import IncidentStore

logger = logging.getLogger("netmon.orchestrator")


class Orchestrator:
    def __init__(
        self,
        config: AppConfig,
        store: IncidentStore,
        remediation: RemediationEngine,
        probe_factory: Callable[[TargetConfig], list[Probe]] | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._remediation = remediation
        self._open_incidents: dict[str, tuple[int, FailureClass]] = {}
        self._targets: dict[str, TargetConfig] = {t.name: t for t in config.targets}
        self.engine = MonitorEngine(
            config,
            on_failure_classified=self._on_failure,
            on_recovered=self.on_target_recovered,
            probe_factory=probe_factory or build_probes,
        )

    # ---- failure path ---------------------------------------------------

    def _on_failure(self, target_cfg: TargetConfig, failure_class: FailureClass) -> None:
        if target_cfg.name in self._open_incidents:
            return  # incident already open
        incident_id = self._store.open_incident(target_cfg.name, failure_class.value)
        self._open_incidents[target_cfg.name] = (incident_id, failure_class)
        metrics.ACTIVE_INCIDENTS.set(len(self._open_incidents))
        logger.error(
            "incident opened",
            extra={
                "target": target_cfg.name,
                "failure_class": failure_class.value,
                "incident_id": incident_id,
            },
        )
        # Remediation is fire-and-forget from the probe loop's perspective:
        # it runs with its own timeouts and can never block monitoring.
        asyncio.ensure_future(self._remediate(target_cfg, failure_class))

    async def _remediate(self, target_cfg: TargetConfig, failure_class: FailureClass) -> None:
        record = await self._remediation.handle_failure(
            target_cfg, failure_class, verifier=self._make_verifier(target_cfg)
        )
        if record is None:
            return
        self._store.record_remediation(
            target_cfg.name, failure_class.value, record.action, record.success, record.detail
        )
        if record.success:
            self._close_incident(target_cfg.name, remediated=True)

    # ---- recovery path ---------------------------------------------------

    def on_target_recovered(self, target_name: str) -> None:
        """Called by the monitoring engine when a target returns to HEALTHY."""
        self._close_incident(target_name, remediated=False)

    def _close_incident(self, target_name: str, remediated: bool) -> None:
        entry = self._open_incidents.pop(target_name, None)
        if entry is None:
            return
        incident_id, failure_class = entry
        self._store.resolve_incident(incident_id, remediated=remediated)
        duration = self._store.incident_duration(incident_id)
        if duration is not None:
            metrics.INCIDENT_DURATION.labels(
                target=target_name, failure_class=failure_class.value
            ).set(duration)
        metrics.ACTIVE_INCIDENTS.set(len(self._open_incidents))
        logger.info(
            "incident closed",
            extra={
                "target": target_name,
                "incident_id": incident_id,
                "remediated": remediated,
                "duration_s": duration,
            },
        )

    # ---- verification -----------------------------------------------------

    def _make_verifier(self, target_cfg: TargetConfig) -> Callable[[], Awaitable[bool]]:
        """A verifier is a single async call returning True when the target
        answers all of its probes successfully once."""

        async def verify() -> bool:
            results = await asyncio.gather(
                *(p.check() for p in build_probes(target_cfg)), return_exceptions=True
            )
            return all(
                not isinstance(r, BaseException) and r.success
                for r in results
            )

        return verify

    # ---- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        await self.engine.run()

    def stop(self) -> None:
        self.engine.stop()
