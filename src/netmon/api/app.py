"""Operational read-only API.

Endpoints expose live state from the orchestrator and durable history
from the store. No CRUD: this is an operations console, not an admin UI.
If NETMON_API_TOKEN is set, every endpoint requires Authorization: Bearer.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Header, HTTPException

from netmon.health.state_machine import HealthState

if TYPE_CHECKING:
    from netmon.orchestrator import Orchestrator

logger = logging.getLogger("netmon.api")

app = FastAPI(title="netmon", version="0.1.0", docs_url="/docs")
_orchestrator: Orchestrator | None = None
_token: str | None = os.environ.get("NETMON_API_TOKEN")


def set_orchestrator(orch: Orchestrator) -> None:
    global _orchestrator
    _orchestrator = orch


def _auth(authorization: str = Header(default="")) -> None:
    if _token and authorization != f"Bearer {_token}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health() -> dict[str, object]:
    orch = _orchestrator
    if orch is None:
        return {"status": "starting"}
    unhealthy = sum(
        1
        for r in orch.engine.runners
        if any(m.state is HealthState.UNHEALTHY for m in r._machines.values())  # noqa: SLF001
    )
    return {
        "status": "degraded" if unhealthy else "ok",
        "unhealthy_targets": unhealthy,
        "open_incidents": len(orch._open_incidents),  # noqa: SLF001
    }


@app.get("/targets", dependencies=[Depends(_auth)])
def targets() -> list[dict[str, object]]:
    orch = _orchestrator
    if orch is None:
        return []
    out: list[dict[str, object]] = []
    for r in orch.engine.runners:
        out.append(
            {
                "name": r.target_cfg.name,
                "host": r.target_cfg.host,
                "checks": [str(p.probe_type.value) for p in r._probes],  # noqa: SLF001
                "states": {
                    str(pt.value): str(m.state.value)
                    for pt, m in r._machines.items()  # noqa: SLF001
                },
            }
        )
    return out


@app.get("/incidents", dependencies=[Depends(_auth)])
def incidents(limit: int = 50) -> list[dict[str, object]]:
    if _orchestrator is None:
        return []
    store = _orchestrator._store  # noqa: SLF001
    return [
        {
            "id": i.id,
            "target": i.target,
            "failure_class": i.failure_class,
            "started_at": i.started_at,
            "resolved_at": i.resolved_at,
            "remediated": i.remediated,
        }
        for i in store.recent_incidents(limit=limit)
    ]


@app.get("/remediations", dependencies=[Depends(_auth)])
def remediations(limit: int = 50) -> list[dict[str, object]]:
    if _orchestrator is None:
        return []
    store = _orchestrator._store  # noqa: SLF001
    return store.remediation_history(limit=limit)


@app.get("/reliability", dependencies=[Depends(_auth)])
def reliability() -> dict[str, object]:
    if _orchestrator is None:
        return {}
    store = _orchestrator._store  # noqa: SLF001
    return {
        "mttr_seconds_overall": store.mttr_seconds(),
        "mttr_seconds_by_target": {
            t.name: store.mttr_seconds(t.name) for t in _orchestrator._config.targets  # noqa: SLF001
        },
    }
