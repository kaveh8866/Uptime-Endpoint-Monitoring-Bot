"""SQLite persistence.

Two tables, both small enough to live on the smallest volume you can attach:

* ``checks``        — append-only history (status, latency, error) for graphing
* ``endpoint_state`` — the current streak counts that drive alerting

SQLite is the datastore on purpose: a monitoring bot that needs a database
server to tell you a service is down has an availability problem of its own.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint    TEXT    NOT NULL,
    checked_at  INTEGER NOT NULL,
    ok          INTEGER NOT NULL,
    status_code INTEGER,
    latency_ms  REAL    NOT NULL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_endpoint_time ON checks (endpoint, checked_at DESC);

CREATE TABLE IF NOT EXISTS endpoint_state (
    endpoint     TEXT PRIMARY KEY,
    state        TEXT    NOT NULL,   -- unknown | up | down
    failures     INTEGER NOT NULL DEFAULT 0,
    successes    INTEGER NOT NULL DEFAULT 0,
    changed_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);
"""


@dataclass
class EndpointState:
    state: str = "unknown"
    failures: int = 0
    successes: int = 0
    changed_at: int = 0


class Storage:
    """Thin wrapper around one SQLite file; no ORM, no connection pool."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=10)
        self._conn.row_factory = sqlite3.Row
        # WAL keeps the writer from blocking history reads on the dashboard side.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record_check(
        self,
        endpoint: str,
        checked_at: int,
        ok: bool,
        latency_ms: float,
        status_code: int | None = None,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO checks (endpoint, checked_at, ok, status_code, latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (endpoint, checked_at, 1 if ok else 0, status_code, latency_ms, error),
        )
        self._conn.commit()

    def load_state(self, endpoint: str) -> EndpointState:
        row = self._conn.execute(
            "SELECT state, failures, successes, changed_at FROM endpoint_state WHERE endpoint = ?",
            (endpoint,),
        ).fetchone()
        if row is None:
            return EndpointState(changed_at=int(time.time()))
        return EndpointState(
            state=row["state"],
            failures=row["failures"],
            successes=row["successes"],
            changed_at=row["changed_at"],
        )

    def save_state(self, endpoint: str, state: EndpointState) -> None:
        now = int(time.time())
        self._conn.execute(
            "INSERT INTO endpoint_state (endpoint, state, failures, successes, changed_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET "
            "  state = excluded.state, failures = excluded.failures, "
            "  successes = excluded.successes, changed_at = excluded.changed_at, "
            "  updated_at = excluded.updated_at",
            (endpoint, state.state, state.failures, state.successes, state.changed_at or now, now),
        )
        self._conn.commit()

    def recent(self, endpoint: str, limit: int = 20) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT checked_at, ok, status_code, latency_ms, error FROM checks "
            "WHERE endpoint = ? ORDER BY checked_at DESC LIMIT ?",
            (endpoint, limit),
        ).fetchall()

    def prune(self, older_than_days: int) -> int:
        """Drop history beyond the retention window; state rows are kept."""
        cutoff = int(time.time()) - older_than_days * 86_400
        cursor = self._conn.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
        self._conn.commit()
        return cursor.rowcount
