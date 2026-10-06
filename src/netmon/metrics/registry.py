"""Central Prometheus metric definitions.

All metric names and labels live here so cardinality stays under control:
the only labels are target, probe, error_class, policy and outcome — never
IPs, timestamps or per-request identifiers. Metric type choices:

- Counters for things that only go up and are consumed as rates.
- Gauges for instantaneous values (state, current success).
- Histograms for latencies: percentiles must aggregate across instances,
  which summaries cannot do.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

PROBE_SUCCESS = Gauge(
    "netmon_probe_success",
    "Whether the last probe run succeeded (1) or not (0)",
    ["target", "probe"],
)

PROBE_DURATION = Histogram(
    "netmon_probe_duration_seconds",
    "Probe round-trip duration",
    ["target", "probe"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

PROBE_FAILURES = Counter(
    "netmon_probe_failures_total",
    "Total probe failures",
    ["target", "probe", "error_class"],
)

TARGET_HEALTH = Gauge(
    "netmon_target_health",
    "Target health state per probe: 0=healthy 1=degraded 2=unhealthy 3=recovering",
    ["target", "probe"],
)

STATE_CHANGES = Counter(
    "netmon_target_state_changes_total",
    "Health state transitions",
    ["target", "probe", "from_state", "to_state"],
)

PACKET_LOSS = Gauge(
    "netmon_packet_loss_ratio",
    "Ratio of failed ICMP probes over the sliding window",
    ["target"],
)

REMEDIATION_ATTEMPTS = Counter(
    "netmon_remediation_attempts_total",
    "Remediation attempts by outcome",
    ["target", "policy", "outcome"],
)

INCIDENT_DURATION = Gauge(
    "netmon_incident_duration_seconds",
    "Duration of the most recent incident per target and failure class",
    ["target", "failure_class"],
)

ACTIVE_INCIDENTS = Gauge(
    "netmon_active_incidents",
    "Currently open incidents",
)


_STATE_VALUES = {"healthy": 0, "degraded": 1, "unhealthy": 2, "recovering": 3}


def export_state(target: str, probe: str, state_name: str) -> None:
    TARGET_HEALTH.labels(target=target, probe=probe).set(_STATE_VALUES.get(state_name, 0))


def export_probe_result(target: str, probe: str, success: bool, latency_ms: float | None) -> None:
    PROBE_SUCCESS.labels(target=target, probe=probe).set(1 if success else 0)
    if latency_ms is not None:
        PROBE_DURATION.labels(target=target, probe=probe).observe(latency_ms / 1000.0)
