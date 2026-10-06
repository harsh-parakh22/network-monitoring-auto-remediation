"""Probe interface and result model.

A Probe is a *stateless measurement*: it takes a target, performs exactly
one check with a hard timeout, and returns a structured ProbeResult.
Probes never retry and never hold state — retries and state transitions
belong to the health evaluator. This separation is what makes probes
trivially unit-testable and the state machine deterministic.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from netmon.config.models import TargetConfig


class ProbeStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"  # probe itself broke (bad config, permission, DNS failure)


class ProbeType(StrEnum):
    ICMP = "icmp"
    TCP = "tcp"
    HTTP = "http"


@dataclass(slots=True)
class ProbeResult:
    target: str
    probe: ProbeType
    status: ProbeStatus
    latency_ms: float | None = None
    error: str | None = None
    error_class: str | None = None  # e.g. "refused", "timeout", "dns", "http_5xx"
    http_status: int | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def success(self) -> bool:
        return self.status is ProbeStatus.OK


class Probe(ABC):
    """Interface for all probes."""

    probe_type: ProbeType

    def __init__(self, target: TargetConfig) -> None:
        self.target = target

    @abstractmethod
    async def check(self) -> ProbeResult: ...
