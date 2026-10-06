# Network Monitoring & Auto-Remediation System

Production-grade network monitoring and automated remediation across simulated Linux nodes.

**Python · Linux · TCP/IP · ICMP · Docker · Prometheus · Grafana**

> Status: under active development — Phases 1–4 complete (architecture, config,
> probes, health engine). Remediation, dashboard, and CI land next.

## What it does

A Python monitoring engine runs **ICMP, TCP and HTTP probes** against simulated
Linux service containers (`app-01`, `app-02`, `db-01`, `chaos-01`) on a real
Docker bridge network. Probe results feed a **hysteretic health state machine**
(HEALTHY → DEGRADED → UNHEALTHY → RECOVERING) per target per probe type, and a
**failure classifier** distinguishes:

| ICMP | TCP | HTTP | Classification |
|---|---|---|---|
| fail | timeout | fail | `host_unreachable` (network layer) |
| ok  | refused | fail | `service_down` (host up, process dead — RST from closed port) |
| ok  | timeout | fail | `port_filtered` (firewall DROP) |
| ok  | ok | 5xx | `app_unhealthy` (transport fine, application broken) |

Everything is exported as **Prometheus metrics** with low-cardinality labels,
logged as **structured JSON**, and evaluated by **Alertmanager alert rules**.
A Grafana dashboard (provisioned as code) visualizes fleet health, latency,
packet loss and remediation outcomes.

## Quick start

```bash
cp .env.example .env
docker compose up -d
docker compose logs -f monitor     # watch probes + state transitions
# Grafana: http://localhost:3000  |  Prometheus: http://localhost:9090
# Monitor API: http://localhost:8000  |  Metrics: http://localhost:9091/metrics
```

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m pytest tests -q
make lint                               # ruff + mypy (strict)
```

## Architecture

See [docs/architecture.md](docs/architecture.md) (in progress). Highlights:

- **Probes are stateless** — one measurement in, one structured result out.
  Retries and thresholds live in the state machine, not the probes.
- **Hysteresis by design** — 1 failure degrades, 3 declare unhealthy, but 2
  consecutive successes are required to recover. A flapping target can never
  toggle alert state.
- **Least privilege** — the monitor runs non-root with only `cap_net_raw`
  (ICMP via the system `ping` binary); chaos nodes get `cap_net_admin` solely
  for `tc netem` injection. Remediation actions are allowlisted functions,
  never shell strings.
- **Config-driven everything** — targets, thresholds, intervals and remediation
  policies come from `configs/config.yaml`, validated by pydantic at startup.

## License

MIT
