# Network Monitoring & Auto-Remediation System

Production-grade network monitoring and automated remediation across simulated Linux nodes.

**Python · Linux · TCP/IP · ICMP · Docker · Prometheus · Grafana**

## What it does

A Python monitoring engine runs **ICMP, TCP and HTTP probes** against simulated
Linux service containers (`app-01`, `app-02`, `db-01`, `chaos-01`) on a real
Docker bridge network. Probe results feed a **hysteretic health state machine**
(HEALTHY → DEGRADED → UNHEALTHY → RECOVERING) per target per probe type, and a
**failure classifier** distinguishes network-layer from transport-layer from
application-layer failures:

| ICMP | TCP | HTTP | Classification |
|---|---|---|---|
| fail | timeout | fail | `host_unreachable` (network layer — packets dropped) |
| ok  | refused | fail | `service_down` (host up, process dead — RST from closed port) |
| ok  | timeout | fail | `port_filtered` (firewall DROP) |
| ok  | ok | 5xx | `app_unhealthy` (transport fine, application broken) |

Failures trigger **config-driven auto-remediation** with three layers of loop
prevention (cooldown, circuit breaker, probe-verified recovery), every attempt
audited to SQLite and Prometheus. Recovery — remediated or natural — closes the
incident and records MTTR.

## Quick start

```bash
cp .env.example .env
docker compose up -d
docker compose logs -f monitor          # live JSON probe/transition logs

# Grafana      http://localhost:3000   (provisioned dashboard: "Network Monitoring")
# Prometheus   http://localhost:9090
# Monitor API  http://localhost:8000/health | /targets | /incidents | /remediations | /reliability
# Raw metrics  http://localhost:9091/metrics
```

### Break something (the fun part)

```bash
docker stop app-01                       # kill a web server
docker compose logs -f monitor           # watch: 3 failures -> UNHEALTHY -> incident
                                         #       -> restart_container -> verified -> recovered
docker start app-01                      # (remediation also does this automatically)
```

All failure scenarios, one command each: [`scripts/failure_injection/inject.sh`](scripts/failure_injection/inject.sh)
(container stop, port failure, latency, packet loss, process crash, HTTP
failure, repeated failures). Full scripted demo: [`scripts/demo/run_demo.sh`](scripts/demo/run_demo.sh).

## Architecture in 60 seconds

```text
Scheduler (per-target interval, asyncio)
  → ICMPProbe / TCPProbe / HTTPProbe  (stateless, hard timeout)
  → HealthStateMachine               (hysteresis: 3 fails -> UNHEALTHY, 2 wins -> HEALTHY)
  → FailureClassifier                (the matrix above)
  → Orchestrator                     (incident open/close, MTTR)
      → RemediationEngine            (policy -> cooldown -> circuit breaker -> allowlisted
                                      action -> probe-verified recovery)
  → Prometheus metrics + JSON logs + SQLite audit trail + Alertmanager rules
```

Details: [docs/architecture.md](docs/architecture.md) ·
[networking.md](docs/networking.md) · [remediation.md](docs/remediation.md) ·
[security.md](docs/security.md) · [observability.md](docs/observability.md) ·
[failure-scenarios.md](docs/failure-scenarios.md) · [testing.md](docs/testing.md)

## Design decisions worth defending

- **Stateless probes, stateful evaluator** — probes answer "what happened
  this time"; the state machine answers "what does it mean over time".
- **Hysteresis** — leaving HEALTHY costs 1 failure, returning costs 2
  consecutive successes. A flapping target can never toggle alerts or
  trigger remediation storms.
- **refused vs timeout preserved end-to-end** — a RST (refused) means
  *restart the service*; silence (timeout) means *restarts are pointless*.
  The classifier's policy lookup encodes exactly that.
- **No policy for network failures** — auto-restarting containers cannot fix
  firewalls or routing; pretending otherwise masks real incidents.
- **Least privilege** — monitor runs non-root with only `NET_RAW` (ICMP via
  the `ping` binary); remediation is an allowlist registry, argv-only
  subprocess, validated container names; config forbids unknown keys.

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"    # Linux/macOS: .venv/bin/pip
pytest tests -q                          # 60+ tests, no Docker needed
make lint                                # ruff + mypy --strict
```

CI (GitHub Actions): ruff → mypy → pytest (Linux: ICMP/RST tests included)
→ Bandit → Docker build → Trivy scan.

## Known limitations

- Single monitor instance (SQLite, in-process scheduler). Multi-instance
  needs leader election or a shard-per-target split — the Store interface
  is the swap point.
- Container restart requires the Docker socket mounted; a per-node agent
  would remove that trust (documented trade-off in docs/security.md).
- Demo network is a single bridge; VLAN/segment simulation is future work.

## License

MIT
