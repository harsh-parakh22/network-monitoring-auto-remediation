# Security Model

## Privileges

| Container | Capabilities | Runs as | Why |
|---|---|---|---|
| monitor | `NET_RAW` only | non-root `monitor` | ICMP via `ping` binary; raw sockets require it, nothing else does |
| chaos-01 | `NET_ADMIN` only | non-root `sim` | `tc netem` failure injection needs it |
| app-01/02, db-01 | none | non-root `sim` | plain HTTP/TCP services need nothing |
| prometheus/grafana/alertmanager | none | image defaults | stock images |

No container is privileged, none runs as root, none mounts the host
filesystem. The Docker socket mount on the monitor is scoped by the
allowlist + name validation (see remediation.md); removing it disables
remediation but not monitoring — documented trade-off.

## Command execution

- No `os.system`, no `shell=True`, anywhere.
- All subprocess calls use argv lists (`ping`, `docker`) with fixed
  binaries and validated arguments.
- Remediation actions are a **registry allowlist**: configuration can only
  *name* an action that exists in code; it cannot introduce a new command.
- Container names validated before use — injection via a crafted name is
  rejected at the boundary (`validate_container_name`), with unit tests
  proving `rm -rf`, `$(...)`, path traversal and whitespace names fail.

## Secrets & config

- Secrets come from environment / `.env` (gitignored, `.env.example`
  documents the shape). Nothing sensitive lives in YAML.
- Config is pydantic-validated with `extra="forbid"` — a typo'd key fails
  startup rather than silently changing behavior.

## API

- Read-only endpoints; no CRUD surface.
- `NETMON_API_TOKEN` (optional, recommended when exposed beyond localhost)
  enforces `Authorization: Bearer` on every endpoint.
- Input validation on all query params (pydantic/FastAPI).

## Network isolation

- `sim_net`: simulated infrastructure. `mon_net`: observability stack.
- Only `monitor` joins both. Target containers cannot reach Grafana or the
  internet; the blast radius of a compromised sim node is its own network.
- Failure-injection endpoints are POST-only, reachable only inside
  `sim_net` — you cannot crash a node from the host's browser.

## CI

- Bandit static security analysis on `src/` every push.
- Trivy image scan (CRITICAL/HIGH) in the docker job.
- Ruff's `S` (flake8-bandit) rules in local lint, tuned with explicit,
  commented exceptions only.
