"""Uptime Endpoint Monitoring Bot — worker loop.

One process, one thread per concern (there is only one), no scheduler library:

1. every endpoint has its own due time, so a 10s check and a 5min check coexist;
2. each probe result is written to SQLite and logged as one JSON line;
3. the state machine turns *streaks* into events, not single blips;
4. only transitions (``down`` / ``recovered``) reach the notifiers.

Shutdown is graceful on SIGTERM/SIGINT so ``docker stop`` between two probes
never leaves a half-written state row.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time

from .checks import CheckResult, run_check
from .config import ConfigError, Endpoint, Thresholds, load_endpoints, load_thresholds
from .notifiers import Channels, dispatch
from .storage import EndpointState, Storage

DEFAULT_STATE_DB = "/data/uptime.db"
PRUNE_INTERVAL_SECONDS = 3600
TICK_SECONDS = 0.5


class JsonFormatter(logging.Formatter):
    """One JSON object per line — directly ingestible by Loki or ``docker logs``."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in ("channel", "event", "endpoint", "status_code", "latency_ms", "error", "state",
                       "delivered", "configured", "transition", "detail", "pruned"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


def apply_result(state: EndpointState, result: CheckResult, thresholds: Thresholds) -> str | None:
    """Advance the streak state machine; return a transition event or ``None``.

    ``unknown`` is not a reportable state: the first successful probe simply
    moves the endpoint to ``up`` without alerting, because a fresh container
    discovering a healthy service is not a recovery.
    """
    now = int(time.time())

    if result.ok:
        state.successes += 1
        state.failures = 0
        if state.state != "up" and state.successes >= thresholds.recovery_threshold:
            transition = "recovered" if state.state == "down" else None
            state.state = "up"
            state.changed_at = now
            return transition
        return None

    state.failures += 1
    state.successes = 0
    if state.state != "down" and state.failures >= thresholds.failure_threshold:
        state.state = "down"
        state.changed_at = now
        return "down"
    return None


def _detail(endpoint: Endpoint, result: CheckResult, thresholds: Thresholds) -> str:
    if not result.ok:
        return result.error or f"status {result.status_code}"
    note = ""
    if result.latency_ms >= thresholds.latency_critical_ms:
        note = " (latency critical)"
    elif result.latency_ms >= thresholds.latency_warn_ms:
        note = " (latency warning)"
    return f"{result.summary()}{note}"


def run_once(
    endpoints: list[Endpoint],
    thresholds: Thresholds,
    storage: Storage,
    channels: Channels,
) -> list[dict]:
    """Probe every endpoint once and return the log records produced.

    Kept free of the loop/timing logic so a future ``--once`` mode and the tests
    can drive it directly.
    """
    log = logging.getLogger("bot.monitor")
    records: list[dict] = []

    for endpoint in endpoints:
        result = run_check(endpoint)
        checked_at = int(time.time())
        storage.record_check(
            endpoint.name,
            checked_at,
            result.ok,
            result.latency_ms,
            status_code=result.status_code,
            error=result.error,
        )

        state = storage.load_state(endpoint.name)
        transition = apply_result(state, result, thresholds)
        storage.save_state(endpoint.name, state)

        detail = _detail(endpoint, result, thresholds)
        if transition:
            dispatch(transition, endpoint.name, detail, channels)

        level = logging.INFO if result.ok else logging.WARNING
        if result.ok and result.latency_ms >= thresholds.latency_critical_ms:
            level = logging.WARNING
        log.log(
            level,
            "check completed",
            extra={
                "endpoint": endpoint.name,
                "status_code": result.status_code,
                "latency_ms": result.latency_ms,
                "error": result.error,
                "state": state.state,
                "transition": transition,
                "detail": detail,
            },
        )
        records.append(
            {
                "endpoint": endpoint.name,
                "target": endpoint.target(),
                "ok": result.ok,
                "latency_ms": result.latency_ms,
                "status_code": result.status_code,
                "state": state.state,
                "transition": transition,
            }
        )

    return records


def main() -> int:
    configure_logging()
    log = logging.getLogger("bot.main")

    try:
        endpoints = load_endpoints()
        thresholds = load_thresholds()
    except ConfigError as exc:
        log.error("configuration rejected", extra={"error": str(exc)})
        return 2

    storage = Storage(os.environ.get("STATE_DB_PATH", DEFAULT_STATE_DB))
    channels = Channels.from_env()
    prune_days = int(os.environ.get("HISTORY_RETENTION_DAYS", "14"))

    log.info(
        "uptime bot starting",
        extra={
            "endpoint": f"{len(endpoints)} endpoint(s)",
            "configured": channels.active(),
            "state": f"failure>={thresholds.failure_threshold}, recovery>={thresholds.recovery_threshold}",
        },
    )

    stop = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("shutdown requested", extra={"detail": f"signal {signum}"})
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Every endpoint is due immediately on start; this is what "checks now" means.
    next_due = {endpoint.name: 0.0 for endpoint in endpoints}
    next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS

    try:
        while not stop.is_set():
            now = time.monotonic()
            due = [endpoint for endpoint in endpoints if next_due[endpoint.name] <= now]

            if due:
                run_once(due, thresholds, storage, channels)
                for endpoint in due:
                    next_due[endpoint.name] = time.monotonic() + endpoint.interval_seconds

            if time.monotonic() >= next_prune:
                removed = storage.prune(prune_days)
                log.info("history pruned", extra={"pruned": removed, "detail": f"retention {prune_days}d"})
                next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS

            stop.wait(TICK_SECONDS)
    finally:
        storage.close()
        log.info("uptime bot stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
