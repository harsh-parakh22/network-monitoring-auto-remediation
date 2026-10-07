# Failure Scenarios

One command each (`scripts/failure_injection/inject.sh <scenario>`), then
`docker compose logs -f monitor` or the Grafana dashboard.

## Scenario 1 — Container stopped (`container-stop`)

```bash
docker stop app-01
```

Expected timeline (10s interval, thresholds 3/2):

```text
t+0..30s   tcp/http probes fail 3x -> UNHEALTHY, incident opens
           classifier: ICMP ok + TCP refused = service_down
t+30s      remediation: restart_container (cooldown, circuit breaker apply)
t+30..40s  verification probes: 2 consecutive successes -> RECOVERED
t+40s      incident closed, remediated=true, MTTR recorded
```

## Scenario 2 — Port failure (`port-fail`)

`docker stop db-01` — db-01 has no HTTP check, so classification comes from
ICMP ok + TCP refused → `service_down`. Demonstrates that classification
works with whatever probes a target actually has.

## Scenario 3 — Latency (`latency`)

`docker exec chaos-01 tc qdisc add dev eth0 root netem delay 300ms 50ms`

Expected: probes keep succeeding but `netmon_probe_duration_seconds` p95
climbs ~300ms; if sustained past 500ms for 2m the `HighLatency` alert
fires. Note nothing "breaks" — this is degradation, the class of failure
monitoring exists for but a dead/alive check cannot see.

## Scenario 4 — Packet loss (`packet-loss`)

`docker exec chaos-01 tc qdisc add dev eth0 root netem loss 30%`

Expected: `netmon_packet_loss_ratio` oscillates around 0.3; ICMP state
flaps DEGRADED↔HEALTHY but **never reaches UNHEALTHY** (hysteresis: 30%
loss can't produce 3 consecutive failures often enough) — demonstrating
false-positive suppression. `PacketLoss` alert still fires from the ratio
metric. That split — state machine for down/up, separate ratio metric for
degradation — is deliberate.

## Scenario 5 — Process crash (`crash-process`)

Kills the sim_node process inside app-01 without stopping the container:
Docker's restart policy revives it. Shows the difference between container
failure (Scenario 1) and process failure, and why we monitor from outside
rather than trusting the container runtime.

## Scenario 6 — Application failure (`http-fail`)

```bash
curl -X POST http://localhost:8082/fail   # or the inject.sh wrapper
```

app-02's `/health` returns 503 while ICMP and TCP stay green →
`app_unhealthy`. The most instructive scenario for interviews: "the
service is *up* — ping works, port answers — and still broken."

## Scenario 7 — Repeated failures

Stop/start app-01 several times within 15 minutes. Expected: the circuit
breaker opens after `max_attempts` failed attempts, then logs
`remediation circuit open — escalating` and fires `RemediationFailed` /
`RepeatedFailure`. This is the anti-loop proof: the system gives up
safely instead of restarting forever.

## Recovery from everything

```bash
./scripts/failure_injection/inject.sh recover-all
```

## Full scripted demo

```bash
./scripts/demo/run_demo.sh
```
