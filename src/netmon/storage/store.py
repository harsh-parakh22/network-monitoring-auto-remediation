"""SQLite persistence for incidents, state transitions and remediation audit.

Why SQLite: one writer (the monitor), low write volume (per incident, not
per probe), zero operations burden, and a real SQL audit trail. Postgres
or any server DB would add an entire service to babysit for no benefit
at this scale. If the monitor ever becomes multi-instance, this is the
component to swap — the Store interface stays the same.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    failure_class TEXT NOT NULL,
    started_at REAL NOT NULL,
    resolved_at REAL,
    remediated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    probe TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    timestamp REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS remediation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    policy TEXT NOT NULL,
    action TEXT NOT NULL,
    success INTEGER NOT NULL,
    detail TEXT NOT NULL,
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_target ON incidents(target, started_at);
CREATE INDEX IF NOT EXISTS idx_transitions_target ON state_transitions(target, timestamp);
"""


@dataclass(slots=True)
class Incident:
    id: int
    target: str
    failure_class: str
    started_at: float
    resolved_at: float | None
    remediated: bool


class IncidentStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- incidents -------------------------------------------------

    def open_incident(self, target: str, failure_class: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO incidents (target, failure_class, started_at) VALUES (?, ?, ?)",
            (target, failure_class, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def resolve_incident(self, incident_id: int, remediated: bool) -> None:
        self._conn.execute(
            "UPDATE incidents SET resolved_at = ?, remediated = ? WHERE id = ?",
            (time.time(), int(remediated), incident_id),
        )
        self._conn.commit()

    def open_incidents(self, target: str | None = None) -> list[Incident]:
        if target:
            rows = self._conn.execute(
                "SELECT * FROM incidents WHERE resolved_at IS NULL AND target = ?"
                " ORDER BY started_at",
                (target,),
            )
        else:
            rows = self._conn.execute(
                "SELECT * FROM incidents WHERE resolved_at IS NULL ORDER BY started_at"
            )
        return [self._row_to_incident(r) for r in rows]

    def recent_incidents(self, limit: int = 50) -> list[Incident]:
        rows = self._conn.execute(
            "SELECT * FROM incidents ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [self._row_to_incident(r) for r in rows]

    def incident_duration(self, incident_id: int) -> float | None:
        row = self._conn.execute(
            "SELECT resolved_at - started_at AS d FROM incidents"
            " WHERE id = ? AND resolved_at IS NOT NULL",
            (incident_id,),
        ).fetchone()
        return row["d"] if row else None

    # ---- transitions & audit ----------------------------------------

    def record_transition(self, target: str, probe: str, from_state: str, to_state: str) -> None:
        self._conn.execute(
            "INSERT INTO state_transitions (target, probe, from_state, to_state, timestamp)"
            " VALUES (?, ?, ?, ?, ?)",
            (target, probe, from_state, to_state, time.time()),
        )
        self._conn.commit()

    def record_remediation(
        self, target: str, policy: str, action: str, success: bool, detail: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO remediation_audit (target, policy, action, success, detail, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (target, policy, action, int(success), detail, time.time()),
        )
        self._conn.commit()

    def remediation_history(
        self, target: str | None = None, limit: int = 50
    ) -> list[dict[str, object]]:
        if target:
            rows = self._conn.execute(
                "SELECT * FROM remediation_audit WHERE target = ? ORDER BY timestamp DESC LIMIT ?",
                (target, limit),
            )
        else:
            rows = self._conn.execute(
                "SELECT * FROM remediation_audit ORDER BY timestamp DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in rows]

    # ---- reliability aggregates ---------------------------------------

    def mttr_seconds(self, target: str | None = None) -> float | None:
        """Mean time to repair over resolved incidents, or None if none resolved."""
        if target:
            row = self._conn.execute(
                "SELECT AVG(resolved_at - started_at) AS m FROM incidents"
                " WHERE resolved_at IS NOT NULL AND target = ?",
                (target,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT AVG(resolved_at - started_at) AS m FROM incidents"
                " WHERE resolved_at IS NOT NULL"
            ).fetchone()
        return row["m"] if row and row["m"] is not None else None

    @staticmethod
    def _row_to_incident(r: sqlite3.Row) -> Incident:
        return Incident(
            id=r["id"],
            target=r["target"],
            failure_class=r["failure_class"],
            started_at=r["started_at"],
            resolved_at=r["resolved_at"],
            remediated=bool(r["remediated"]),
        )
