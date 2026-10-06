"""Failure classification.

Maps a target's per-probe outcomes to a failure class, which is what the
remediation engine keys off. The matrix encodes TCP/IP diagnosis:

- ICMP fails AND TCP times out      -> host_unreachable (network layer:
                                       packets not getting there at all)
- ICMP ok, TCP refused              -> service_down (host up, RST from
                                       closed port = process dead)
- ICMP ok, TCP times out            -> port_filtered (firewall DROP)
- TCP ok, HTTP fails                -> app_unhealthy (transport fine,
                                       application misbehaving)

Precondition: the evaluator only classifies once a target is UNHEALTHY,
so at least one check is failing.
"""

from __future__ import annotations

from enum import StrEnum

from netmon.health.state_machine import HealthState, HealthStateMachine


class FailureClass(StrEnum):
    HOST_UNREACHABLE = "host_unreachable"
    PORT_FILTERED = "port_filtered"
    SERVICE_DOWN = "service_down"
    APP_UNHEALTHY = "app_unhealthy"
    UNKNOWN = "unknown"


class FailureClassifier:
    def __init__(self) -> None:
        self._machines: dict[str, HealthStateMachine] = {}

    def register(self, probe_key: str, machine: HealthStateMachine) -> None:
        """probe_key: '<target>:<probe_type>'"""
        self._machines[probe_key] = machine

    def classify(self, target: str) -> FailureClass:
        states: dict[str, HealthState] = {}
        last_errors: dict[str, str | None] = {}
        prefix = f"{target}:"
        for probe_key, m in self._machines.items():
            if not probe_key.startswith(prefix):
                continue
            probe = probe_key[len(prefix):]
            states[probe] = m.state
            last_errors[probe] = m.last_error_class
        if not states or not any(
            s in (HealthState.UNHEALTHY, HealthState.RECOVERING) for s in states.values()
        ):
            return FailureClass.UNKNOWN

        icmp = states.get("icmp")
        tcp = states.get("tcp")
        http = states.get("http")

        icmp_ok = icmp in (None, HealthState.HEALTHY)
        tcp_ok = tcp in (None, HealthState.HEALTHY)

        if not icmp_ok and not tcp_ok:
            return FailureClass.HOST_UNREACHABLE
        if icmp_ok and tcp is HealthState.UNHEALTHY:
            # Distinguish refused (process dead) from timeout (firewall DROP)
            # via the last observed TCP error class.
            return (
                FailureClass.SERVICE_DOWN
                if last_errors.get("tcp") == "refused"
                else FailureClass.PORT_FILTERED
            )
        if tcp_ok and http is HealthState.UNHEALTHY:
            return FailureClass.APP_UNHEALTHY
        if icmp is None and tcp is HealthState.UNHEALTHY and http is None:
            return FailureClass.SERVICE_DOWN
        return FailureClass.UNKNOWN
