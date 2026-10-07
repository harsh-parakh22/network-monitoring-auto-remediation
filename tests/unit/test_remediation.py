"""Remediation engine tests.

These prove the safety properties, not just the happy path:
- unknown action names are refused (allowlist)
- cooldown suppresses rapid repeat attempts
- circuit breaker stops attempts after max failures in the window
- recovery verification: success only counts when probes confirm
"""

from __future__ import annotations

import pytest

from netmon.config.models import RemediationConfig, RemediationPolicyConfig
from netmon.health.classifier import FailureClass
from netmon.remediation.actions import ActionError, ActionRegistry, ActionResult
from netmon.remediation.engine import RemediationEngine


def make_config(**policy_overrides) -> RemediationConfig:
    policy = RemediationPolicyConfig(
        action="restart_container",
        max_attempts=2,
        cooldown_seconds=60,
        attempts_window_seconds=900,
        verify_interval_seconds=0.01,
        verify_timeout_seconds=0.05,
    )
    for k, v in policy_overrides.items():
        setattr(policy, k, v)
    return RemediationConfig(policies={"service_down": policy})


class FakeTarget:
    name = "app-01"


def make_engine(config=None, action_result=True, verifier=None):
    config = config or make_config()
    registry = ActionRegistry()
    calls: list[str] = []

    async def fake_action(target: str) -> ActionResult:
        calls.append(target)
        return ActionResult(success=action_result, detail="fake")

    registry.register("restart_container", fake_action)
    return RemediationEngine(config, registry=registry, verifier=verifier), calls


@pytest.mark.parametrize("bad", ["app_1; rm -rf /", "../etc", "$(id)", "a b", ""])
def test_dangerous_container_names_rejected(bad):
    from netmon.remediation.actions import validate_container_name

    with pytest.raises(ActionError):
        validate_container_name(bad)


async def test_unknown_action_name_is_refused():
    config = make_config()
    config.policies["service_down"].action = "format_disk"  # not registered
    engine, calls = make_engine(config)
    record = await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    assert record is not None and not record.success
    assert "allowlist" in record.detail
    assert calls == []


async def test_remediation_success_with_verified_recovery():
    async def verifier() -> bool:
        return True

    engine, calls = make_engine(verifier=verifier)
    record = await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    assert record is not None and record.success
    assert record.detail == "recovery verified"
    assert calls == ["app-01"]


async def test_unverified_recovery_counts_as_failure():
    async def verifier() -> bool:
        return False

    engine, _ = make_engine(verifier=verifier)
    record = await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    assert record is not None and not record.success
    assert "NOT verified" in record.detail


async def test_cooldown_blocks_rapid_repeated_attempts():
    engine, calls = make_engine()
    await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    record2 = await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    assert record2 is None  # cooldown active
    assert len(calls) == 1


async def test_circuit_breaker_stops_after_max_attempts():
    async def never_recovers() -> bool:
        return False

    engine, calls = make_engine(verifier=never_recovers)
    # Attempt 1: executes, fails (unverified). Attempt 2 would be in
    # cooldown, so simulate the cooldown expiring and fail again.
    await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    engine._last_attempt.clear()  # time-travel past the cooldown
    await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    engine._last_attempt.clear()
    # Attempt 3: circuit open (2 failures / window) — skipped entirely.
    record = await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    assert record is None
    assert len(calls) == 2
    assert len(engine.audit) == 2


async def test_no_policy_means_no_remediation():
    engine, calls = make_engine()
    record = await engine.handle_failure(FakeTarget(), FailureClass.HOST_UNREACHABLE)
    assert record is None
    assert calls == []


async def test_disabled_config_means_no_remediation():
    config = make_config()
    config.enabled = False
    engine, calls = make_engine(config)
    record = await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    assert record is None
    assert calls == []


async def test_audit_trail_records_everything():
    engine, _ = make_engine()
    await engine.handle_failure(FakeTarget(), FailureClass.SERVICE_DOWN)
    assert len(engine.audit) == 1
    assert engine.audit[0].target == "app-01"
    assert engine.audit[0].policy == "service_down"
