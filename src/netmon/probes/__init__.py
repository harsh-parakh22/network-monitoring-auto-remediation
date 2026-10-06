"""Probe factory: builds the probe set for a target from its config."""

from __future__ import annotations

from netmon.config.models import ProbeType, TargetConfig
from netmon.probes.base import Probe
from netmon.probes.http import HTTPProbe
from netmon.probes.icmp import ICMPProbe
from netmon.probes.tcp import TCPProbe


def build_probes(target: TargetConfig) -> list[Probe]:
    probes: list[Probe] = []
    for check in target.checks:
        match check:
            case ProbeType.ICMP:
                probes.append(ICMPProbe(target))
            case ProbeType.TCP:
                probes.append(TCPProbe(target))
            case ProbeType.HTTP:
                probes.append(HTTPProbe(target))
    return probes
