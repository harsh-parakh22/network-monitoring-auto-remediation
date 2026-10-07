"""The remediation engine: classification in, safe verified action out.

Safety model — three independent layers prevent runaway remediation:

1. Cooldown: after any attempt for (target, policy), wait cooldown_seconds
   before another attempt — rate limiting.
2. Circuit breaker: max_attempts within attempts_window_seconds; when
   exceeded, stop remediating entirely (escalate: alert only). No infinite
   retry loops, by construction.
3. Verification: an action only counts as remediation if probes confirm
   recovery afterwards; otherwise the attempt counts as failed and burns
   budget — repeated futile restarts are cut off by layer 2.

Every attempt is recorded as a structured audit event and a Prometheus
counter, so remediation behavior is fully observable after the fact.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from netmon.config.models import RemediationConfig, RemediationPolicyConfig, TargetConfig
from netmon.health.classifier import FailureClass
from netmon.metrics import registry as metrics
from netmon.remediation.actions import ActionError, ActionRegistry, default_registry

logger = logging.getLogger("netmon.remediation")


@dataclass(slots=True)
class AttemptRecord:
    target: str
    policy: str
    action: str
    success: bool
    detail: str
    timestamp: float = field(default_factory=time.time)


class RemediationEngine:
    def __init__(
        self,
        config: RemediationConfig,
        registry: ActionRegistry | None = None,
        verifier: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or default_registry()
        self._verifier = verifier  # async () -> bool: "did the target recover?"
        self._last_attempt: dict[tuple[str, str], float] = {}
        self._attempts: dict[tuple[str, str], deque[float]] = {}
        self.audit: list[AttemptRecord] = []

    # ---- safety layers -------------------------------------------------

    def _in_cooldown(self, target: str, policy_cfg: RemediationPolicyConfig) -> bool:
        key = (target, policy_cfg.action)
        last = self._last_attempt.get(key)
        return last is not None and (time.time() - last) < policy_cfg.cooldown_seconds

    def _circuit_open(self, target: str, policy_cfg: RemediationPolicyConfig) -> bool:
        """True when max_attempts failures occurred within the window."""
        key = (target, policy_cfg.action)
        history = self._attempts.setdefault(key, deque())
        cutoff = time.time() - policy_cfg.attempts_window_seconds
        while history and history[0] < cutoff:
            history.popleft()
        return len(history) >= policy_cfg.max_attempts

    # ---- main entry ----------------------------------------------------

    async def handle_failure(
        self,
        target: TargetConfig,
        failure_class: FailureClass,
        verifier: Callable[[], Awaitable[bool]] | None = None,
    ) -> AttemptRecord | None:
        """Called by the monitoring engine when a target is declared unhealthy.

        Returns the attempt record, or None when remediation was skipped
        (no policy / disabled / cooldown / circuit open).
        """
        if not self._config.enabled:
            return None
        policy_name = failure_class.value
        policy_cfg = self._config.policies.get(policy_name)
        if policy_cfg is None:
            logger.info(
                "no remediation policy for failure class",
                extra={"target": target.name, "failure_class": policy_name},
            )
            return None

        if self._circuit_open(target.name, policy_cfg):
            logger.error(
                "remediation circuit open — escalating, no further attempts",
                extra={"target": target.name, "policy": policy_name},
            )
            metrics.REMEDIATION_ATTEMPTS.labels(
                target=target.name, policy=policy_name, outcome="escalated"
            ).inc()
            return None
        if self._in_cooldown(target.name, policy_cfg):
            logger.info(
                "remediation skipped: cooldown active",
                extra={"target": target.name, "policy": policy_name},
            )
            return None

        record = await self._execute(target, policy_name, policy_cfg, verifier or self._verifier)
        self._record(target.name, policy_name, record)
        return record

    async def _execute(
        self,
        target: TargetConfig,
        policy_name: str,
        policy_cfg: RemediationPolicyConfig,
        verifier: Callable[[], Awaitable[bool]] | None,
    ) -> AttemptRecord:
        try:
            action = self._registry.get(policy_cfg.action)
        except ActionError as e:
            return AttemptRecord(target.name, policy_name, policy_cfg.action, False, str(e))

        try:
            result = await action(target.name)
        except Exception as e:
            return AttemptRecord(
                target.name, policy_name, policy_cfg.action, False, f"action raised: {e}"
            )

        if not result.success:
            return AttemptRecord(target.name, policy_name, policy_cfg.action, False, result.detail)

        # Action succeeded — now verify recovery if a verifier is wired in.
        verified = True
        if self._verifier is not None:
            deadline = time.time() + policy_cfg.verify_timeout_seconds
            verified = False
            while time.time() < deadline:
                if await self._verifier():
                    verified = True
                    break
                await asyncio.sleep(policy_cfg.verify_interval_seconds)

        detail = "recovery verified" if verified else "action succeeded but recovery NOT verified"
        return AttemptRecord(target.name, policy_name, policy_cfg.action, verified, detail)

    def _record(self, target_name: str, policy_name: str, record: AttemptRecord) -> None:
        self.audit.append(record)
        key = (target_name, record.action)
        self._last_attempt[key] = record.timestamp
        # Only failed attempts consume circuit-breaker budget: a verified
        # success resets nothing but also shouldn't count against retries.
        if not record.success:
            self._attempts.setdefault(key, deque()).append(record.timestamp)
        metrics.REMEDIATION_ATTEMPTS.labels(
            target=target_name, policy=policy_name,
            outcome="success" if record.success else "failed",
        ).inc()
        logger.info(
            "remediation attempt",
            extra={
                "target": target_name,
                "policy": policy_name,
                "action": record.action,
                "success": record.success,
                "detail": record.detail,
            },
        )
