# Testing

## Test pyramid

| Layer | What it proves | How |
|---|---|---|
| Unit (37+) | Config validation, probe semantics, state machine transitions, classifier matrix, cooldown/circuit-breaker math, store persistence | Real loopback sockets and real SQLite; only network/Docker boundaries are faked |
| Engine-level | Full failure→classification journeys through the real `TargetRunner` | Scripted fake probes |
| Lifecycle (E2E) | Failure → incident → remediation → verified recovery → closed incident, through the real `Orchestrator` + store | One scripted-probe scenario |
| Integration (container) | Monitors real targets, exports real metrics | `make up`, see below |
| Failure scenarios | Every scenario in failure-scenarios.md reproduces on demand | `scripts/failure_injection/` |

## Principles

- **No mocks of the code under test.** TCP/HTTP probes are tested against
  real asyncio servers and a real `http.server`; refused/timeout semantics
  are asserted, not assumed. The only fakes are *boundaries* (Docker,
  ICMP binaries) and *time-dependent outcomes* (scripted probes).
- **Platform honesty.** RST-on-closed-port and ICMP tests skip on Windows
  (firewall swallows RSTs; `ping` flags differ) and run on Linux CI and in
  containers. Fake degradation would be worse than a skip.
- **Safety properties have their own tests.** Cooldown suppression,
  circuit-breaker cutoff after N attempts, allowlist rejection of
  `rm -rf`/`$(...)`/traversal names — these are the tests that keep
  auto-remediation safe to trust.

## Running

```bash
pytest tests -q                 # full suite
pytest tests/unit -q            # no Docker needed
pytest --cov=netmon tests       # coverage
```

## The E2E lifecycle test (worth reading)

`tests/unit/test_orchestrator.py` scripts a probe to fail 3× then recover,
registers a fake restart action, and drives the real Orchestrator. It then
asserts the whole trail: exactly one restart action executed, recovery
verified, one resolved-and-remediated incident in SQLite, MTTR measurable,
zero open incidents. It is the system's contract in one test.
