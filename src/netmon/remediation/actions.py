"""Remediation actions: the only way the system may change the world.

Security model:
- Actions are registered by name in an explicit allowlist. The remediation
  engine can never execute anything that is not registered here — config
  files name an action, they never carry commands.
- Every action receives only typed, validated arguments (container name is
  checked against ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$) — never interpolated into
  a shell string.
- Docker is driven through the official Python SDK — no CLI subprocess,
  no shell, no string interpolation anywhere.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("netmon.remediation")

_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

_DOCKER_TIMEOUT = 30  # seconds for any single docker API call


class ActionError(Exception):
    """Raised when an action is invalid, unknown, or fails."""


@dataclass(slots=True)
class ActionResult:
    success: bool
    detail: str


# Signature: every action takes the target name and returns an ActionResult.
Action = Callable[[str], Awaitable[ActionResult]]


def validate_container_name(name: str) -> str:
    if not _CONTAINER_NAME_RE.fullmatch(name) or len(name) > 63:
        raise ActionError(f"invalid container name: {name!r}")
    return name


async def _run_container_op(target: str, op: str) -> ActionResult:
    """restart/start via the docker SDK, executed off the event loop."""
    name = validate_container_name(target)

    def _do() -> Any:
        import docker

        client = docker.from_env(timeout=_DOCKER_TIMEOUT)
        getattr(client.containers.get(name), op)(timeout=10)

    try:
        await asyncio.wait_for(asyncio.to_thread(_do), timeout=_DOCKER_TIMEOUT)
    except Exception as e:
        detail = f"docker {op} failed: {e.__class__.__name__}: {e}"
        logger.warning(
            "remediation docker op failed",
            extra={"target": name, "detail": detail},
        )
        return ActionResult(success=False, detail=detail)
    logger.info("remediation action: %s_container", op, extra={"target": name})
    return ActionResult(success=True, detail=f"{op} ok")


async def restart_container(target: str) -> ActionResult:
    """Restart the Docker container named after the target."""
    return await _run_container_op(target, "restart")


async def start_container(target: str) -> ActionResult:
    """Start a stopped Docker container (no-op if already running)."""
    return await _run_container_op(target, "start")


class ActionRegistry:
    """The allowlist. Everything the remediation engine may do is here."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, name: str, action: Action) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ActionError(f"invalid action name: {name!r}")
        self._actions[name] = action

    def get(self, name: str) -> Action:
        try:
            return self._actions[name]
        except KeyError as e:
            raise ActionError(f"action {name!r} is not in the allowlist") from e

    def names(self) -> list[str]:
        return sorted(self._actions)


def default_registry() -> ActionRegistry:
    registry = ActionRegistry()
    registry.register("restart_container", restart_container)
    registry.register("start_container", start_container)
    return registry
