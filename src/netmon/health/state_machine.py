"""Health state machine: probe results in, state transitions out.

The machine implements hysteresis — it takes fewer consecutive failures
to leave HEALTHY than consecutive successes to return to it. One lost
packet on a real network is noise; alerting on it causes false positives
and alert fatigue. Requiring more evidence to *recover* than to *fail*
prevents flapping when a service alternates up/down probe-to-probe.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from netmon.config.models import MonitoringConfig
from netmon.probes.base import ProbeResult


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    RECOVERING = "recovering"


@dataclass(slots=True)
class StateTransition:
    from_state: HealthState
    to_state: HealthState
    timestamp: float


class HealthStateMachine:
    """Tracks one target's state for one probe type.

    Transitions:
      HEALTHY -> DEGRADED   on first failure (early warning, not actionable yet)
      DEGRADED -> UNHEALTHY on failure_threshold consecutive failures
      UNHEALTHY -> RECOVERING on first success after UNHEALTHY
      RECOVERING -> HEALTHY on recovery_threshold consecutive successes
      RECOVERING -> UNHEALTHY if a failure occurs during recovery
      DEGRADED -> HEALTHY   on any success (transient blip resolved itself)
    """

    def __init__(self, config: MonitoringConfig) -> None:
        self._config = config
        self.state = HealthState.HEALTHY
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self.last_error_class: str | None = None

    def record(self, result: ProbeResult) -> StateTransition | None:
        """Feed one probe result; returns a StateTransition if state changed."""
        before = self.state
        success = result.success
        if result.error_class is not None:
            self.last_error_class = result.error_class

        if success:
            self._consecutive_successes += 1
            self._consecutive_failures = 0
            if self.state is HealthState.HEALTHY:
                pass
            elif self.state is HealthState.DEGRADED:
                self.state = HealthState.HEALTHY
            elif self.state is HealthState.UNHEALTHY:
                self.state = HealthState.RECOVERING
            elif self.state is HealthState.RECOVERING:
                if self._consecutive_successes >= self._config.recovery_threshold:
                    self.state = HealthState.HEALTHY
        else:
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            if self.state is HealthState.HEALTHY:
                if self._consecutive_failures >= self._config.degraded_threshold:
                    self.state = HealthState.DEGRADED
            elif self.state is HealthState.DEGRADED:
                if self._consecutive_failures >= self._config.failure_threshold:
                    self.state = HealthState.UNHEALTHY
            elif self.state in (HealthState.UNHEALTHY, HealthState.RECOVERING):
                # A failure during recovery resets the recovery streak and
                # drops the target back to UNHEALTHY — no partial credit.
                self.state = HealthState.UNHEALTHY

        if self.state is not before:
            return StateTransition(before, self.state, time.time())
        return None

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def consecutive_successes(self) -> int:
        return self._consecutive_successes

