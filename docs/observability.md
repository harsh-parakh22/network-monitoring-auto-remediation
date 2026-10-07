# Observability

## Metric catalog

Prefix `netmon_`. Labels are limited to `target`, `probe`, `error_class`,
`policy`, `outcome`, `from_state`, `to_state` — all bounded sets. No IPs,
no timestamps, no per-request identifiers: cardinality stays flat no
matter how long the system runs.

| Metric | Type | Why this type |
|---|---|---|
| `netmon_probe_success{target,probe}` | Gauge | Instantaneous last-result; gauges are for "what is true now" |
| `netmon_probe_duration_seconds{target,probe}` | Histogram | Latency percentiles must aggregate across instances; summaries can't |
| `netmon_probe_failures_total{target,probe,error_class}` | Counter | Failures only accumulate; rates (`rate()`) is the Prometheus idiom |
| `netmon_target_health{target,probe}` | Gauge (enum 0–3) | State machine export; 0=healthy 1=degraded 2=unhealthy 3=recovering |
| `netmon_target_state_changes_total` | Counter | Flapping detection (`increase(...[1h]) >= 3` alerts) |
| `netmon_packet_loss_ratio{target}` | Gauge | Sliding-window ratio over ICMP results |
| `netmon_remediation_attempts_total{target,policy,outcome}` | Counter | outcome ∈ success / failed / escalated |
| `netmon_incident_duration_seconds{target,failure_class}` | Gauge | MTTR per incident, aggregated by Grafana |
| `netmon_active_incidents` | Gauge | Live incident count |

## Logging

One JSON object per line on stdout: `ts, level, component, message` plus
event context (`target`, `probe`, `state`, `error_class`, `incident_id`,
`policy`, `outcome`...). `incident_id` correlates every line belonging to
one incident — from first failure to remediation attempt to closure.
Consumed via `docker logs monitor | jq 'select(.level=="ERROR")'` today;
Loki is a drop-in later (stdout format already structured).

## Alerting (Alertmanager)

Rules in `infrastructure/prometheus/rules.yml`:

| Alert | Condition | Severity |
|---|---|---|
| TargetDown | any probe state == 2 for 1m | critical |
| HighLatency | probe p95 > 500ms for 2m | warning |
| PacketLoss | loss ratio > 5% for 2m | warning |
| RemediationFailed | any failed attempt in 15m | critical (human needed) |
| RepeatedFailure | ≥3 unhealthy transitions/hour | warning (flapping or recurring fault) |

Grouping by `alertname, target`, 30s group_wait, 4h repeat interval —
deduplication and silencing are Alertmanager's job, not custom code.

## Dashboard

Provisioned as code (`infrastructure/grafana/dashboards/netmon-overview.json`):
Overview (fleet counts, active incidents, MTTR) → Health & Latency (state
timeline, p95/median) → Network (packet loss, TCP failures split by
refused/timeout) → Remediation (outcomes, flapping detector). Everything
an on-call needs to answer "what broke, when, what did the system do
about it, did it work" without touching a terminal.
