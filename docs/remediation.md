# Remediation Design

## Flow

```text
UNHEALTHY + classified
  → policy lookup by failure class (configs/config.yaml)
  → circuit breaker: max_attempts within attempts_window_seconds?
      open → ESCALATE (alert only, no more actions)
  → cooldown: last attempt < cooldown_seconds ago?
      yes → skip this cycle
  → execute allowlisted action (typed args, no shell)
  → verify: re-run target's probes until recovery_threshold
      confirmed or verify_timeout_seconds elapsed
  → success: incident closed, MTTR recorded
    failure: attempt burns circuit-breaker budget
```

## Three layers of loop prevention

| Layer | Mechanism | Prevents |
|---|---|---|
| Cooldown | One attempt per `cooldown_seconds` per (target, policy) | Rapid retry hammering a dying service |
| Circuit breaker | `max_attempts` failures within a sliding window stops all attempts | Infinite restart loops on unfixable failures |
| Verification | Success only counts when probes observe recovery | "Restart succeeded" claims with no evidence; futile restarts burn budget and get cut off by layer 2 |

## Policies (config-driven)

```yaml
remediation:
  policies:
    service_down:          # TCP refused — process dead, restart will help
      action: restart_container
      max_attempts: 3
      cooldown_seconds: 60
      attempts_window_seconds: 900
    container_down:
      action: restart_container
      max_attempts: 2
      cooldown_seconds: 120
```

Deliberately **no policy for** `host_unreachable` or `port_filtered`:
restarting a container cannot fix a broken network or a firewall rule.
Remediation that can't help is remediation that masks the real problem —
those classes alert a human instead.

## Security of actions

- Actions live in an **allowlist registry** (`remediation/actions.py`).
  Config names an action; it never carries a command string.
- Container names are validated against `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$`
  before touching Docker.
- `docker` CLI is invoked with an **argv list** — no shell, no
  interpolation, nothing to inject.
- Every attempt produces: a structured log line, a Prometheus counter
  (`netmon_remediation_attempts_total{outcome=...}`), and a durable row in
  `remediation_audit` — full auditability.

## Idempotency

`docker restart` and `docker start` are idempotent: restarting an already-
running container is safe; starting a running container is a no-op. Combined
with cooldowns, duplicate or replayed remediation triggers are harmless.

## Blast radius

The monitor can only restart containers whose names match configured
targets. It cannot stop containers, execute arbitrary commands, or touch
anything outside its validated target list. Worst case for a bad policy:
a bounded number of restarts of one target container.
