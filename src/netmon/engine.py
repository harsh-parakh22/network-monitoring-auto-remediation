"""The monitoring engine: owns the probe loop and health evaluation.

Design notes:
- One asyncio task per target runs its probes sequentially on that
  target's interval; probes across targets run concurrently. A hung probe
  cannot block the rest of the fleet because each probe has a hard
  asyncio.wait_for timeout.
- The engine is agnostic to probe implementation: callers inject
  build_probes (dependency injection), which makes the whole engine
  testable with fake probes and virtual time.
- On every state transition we export metrics, log a structured event,
  and (once unhealthy) classify the failure. Remediation hooks in via the
  on_failure callback — the engine stays out of the remediation business.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable

from netmon.config.models import AppConfig, MonitoringConfig, TargetConfig
from netmon.health.classifier import FailureClass, FailureClassifier
from netmon.health.state_machine import HealthState, HealthStateMachine
from netmon.metrics import registry as metrics
from netmon.probes import build_probes
from netmon.probes.base import Probe, ProbeResult, ProbeStatus, ProbeType

logger = logging.getLogger("netmon.engine")

_STATE_LOG_NAMES = {s.value for s in HealthState}


class TargetRunner:
    """Runs one target's probes on schedule and evaluates health."""

    def __init__(
        self,
        config: MonitoringConfig,
        target_cfg: TargetConfig,
        probes: list[Probe] | None = None,
        on_failure_classified: Callable[[TargetConfig, FailureClass], None] | None = None,
        on_recovered: Callable[[str], None] | None = None,
    ) -> None:
        self.target_cfg = target_cfg
        self._config = config
        self._probes = probes if probes is not None else build_probes(target_cfg)
        self._machines: dict[ProbeType, HealthStateMachine] = {
            p.probe_type: HealthStateMachine(config) for p in self._probes
        }
        self._classifier = FailureClassifier()
        for probe_type, machine in self._machines.items():
            self._classifier.register(f"{target_cfg.name}:{probe_type.value}", machine)
        self._loss_window: deque[bool] = deque(maxlen=config.packet_loss_window)
        self._on_failure_classified = on_failure_classified
        self._on_recovered = on_recovered
        self.incident_open = False

    async def run_once(self) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        for probe in self._probes:
            try:
                result = await asyncio.wait_for(
                    probe.check(), timeout=self.target_cfg.timeout_seconds + 5
                )
            except TimeoutError:
                result = ProbeResult(
                    target=self.target_cfg.name,
                    probe=probe.probe_type,
                    status=ProbeStatus.TIMEOUT,
                    error="probe wrapper timeout",
                    error_class="timeout",
                )
            results.append(result)
            self._process(probe.probe_type, result)
        return results

    def _process(self, probe_type: ProbeType, result: ProbeResult) -> None:
        name = self.target_cfg.name
        machine = self._machines[probe_type]
        transition = machine.record(result)

        metrics.export_probe_result(name, probe_type.value, result.success, result.latency_ms)
        metrics.export_state(name, probe_type.value, machine.state.value)
        if not result.success:
            metrics.PROBE_FAILURES.labels(
                target=name, probe=probe_type.value, error_class=result.error_class or "unknown"
            ).inc()
        if probe_type is ProbeType.ICMP:
            self._loss_window.append(result.success)
            if self._loss_window:
                metrics.PACKET_LOSS.labels(target=name).set(
                    1 - sum(self._loss_window) / len(self._loss_window)
                )

        log_extra = {
            "target": name,
            "probe": probe_type.value,
            "status": result.status.value,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "error_class": result.error_class,
            "state": machine.state.value,
        }
        if transition is not None:
            metrics.STATE_CHANGES.labels(
                target=name,
                probe=probe_type.value,
                from_state=transition.from_state.value,
                to_state=transition.to_state.value,
            ).inc()
            logger.info(
                "state transition %s -> %s",
                transition.from_state.value,
                transition.to_state.value,
                extra=log_extra,
            )
            if transition.to_state is HealthState.UNHEALTHY:
                self._handle_unhealthy()
            elif transition.to_state is HealthState.HEALTHY:
                self.incident_open = False
                if self._on_recovered is not None:
                    self._on_recovered(name)
        else:
            logger.debug("probe result", extra=log_extra)

    def _handle_unhealthy(self) -> None:
        if self.incident_open:
            return
        failure_class = self._classifier.classify(self.target_cfg.name)
        self.incident_open = True
        logger.error(
            "target unhealthy",
            extra={
                "target": self.target_cfg.name,
                "failure_class": failure_class.value,
                "states": {p.value: m.state.value for p, m in self._machines.items()},
            },
        )
        if self._on_failure_classified is not None:
            self._on_failure_classified(self.target_cfg, failure_class)


class MonitorEngine:
    """Schedules all target runners until stopped."""

    def __init__(
        self,
        config: AppConfig,
        on_failure_classified: Callable[[TargetConfig, FailureClass], None] | None = None,
        on_recovered: Callable[[str], None] | None = None,
        probe_factory: Callable[[TargetConfig], list[Probe]] = build_probes,
    ) -> None:
        self._config = config
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self.runners: list[TargetRunner] = [
            TargetRunner(
                config.monitoring,
                t,
                probes=None if probe_factory is build_probes else probe_factory(t),
                on_failure_classified=on_failure_classified,
                on_recovered=on_recovered,
            )
            for t in config.targets
        ]

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._tasks = [asyncio.create_task(self._loop(r)) for r in self.runners]
        await self._stop.wait()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _loop(self, runner: TargetRunner) -> None:
        interval = runner.target_cfg.interval_seconds
        while not self._stop.is_set():
            try:
                await runner.run_once()
            except Exception:
                # A runner crash must never take down the whole engine.
                logger.exception("runner crashed", extra={"target": runner.target_cfg.name})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass
