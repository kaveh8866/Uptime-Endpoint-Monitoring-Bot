"""Health probes.

Each probe returns a :class:`CheckResult` and never raises: a timeout, a refused
connection and an unexpected status code are all *data*, because "the endpoint
did not answer" is exactly what this bot exists to report.
"""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Endpoint, HTTP_KINDS

USER_AGENT = "cloudguard-uptime-bot/0.1"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one probe, independent of any alerting decision."""

    ok: bool
    latency_ms: float
    status_code: int | None = None
    error: str | None = None

    def summary(self) -> str:
        if self.ok:
            return f"{self.status_code or 'ok'} in {self.latency_ms:.0f}ms"
        detail = self.error or f"status {self.status_code}"
        return f"{detail} after {self.latency_ms:.0f}ms"


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def check_http(endpoint: Endpoint) -> CheckResult:
    """Probe an HTTP(S) endpoint and compare the status against the expectation."""
    request = urllib.request.Request(
        endpoint.url or "",
        method=endpoint.method,
        headers={"User-Agent": USER_AGENT, **endpoint.headers},
    )
    context = ssl.create_default_context()
    started = time.monotonic()

    try:
        with urllib.request.urlopen(request, timeout=endpoint.timeout_seconds, context=context) as response:
            latency = _elapsed_ms(started)
            status = response.status
            return CheckResult(ok=status in endpoint.expected_status, latency_ms=latency, status_code=status)
    except urllib.error.HTTPError as exc:
        # Reachable but unhappy: keep the status code, it is the useful signal.
        latency = _elapsed_ms(started)
        return CheckResult(
            ok=exc.code in endpoint.expected_status,
            latency_ms=latency,
            status_code=exc.code,
            error=None if exc.code in endpoint.expected_status else f"unexpected status {exc.code}",
        )
    except urllib.error.URLError as exc:
        return CheckResult(ok=False, latency_ms=_elapsed_ms(started), error=f"{exc.reason}")
    except (TimeoutError, socket.timeout):
        return CheckResult(ok=False, latency_ms=_elapsed_ms(started), error="timeout")
    except Exception as exc:  # noqa: BLE001 - a probe must never kill the worker
        return CheckResult(ok=False, latency_ms=_elapsed_ms(started), error=f"{type(exc).__name__}: {exc}")


def check_tcp(endpoint: Endpoint) -> CheckResult:
    """Measure how long a TCP handshake takes; success means the port answers."""
    started = time.monotonic()
    try:
        with socket.create_connection((endpoint.host or "", endpoint.port or 0), timeout=endpoint.timeout_seconds):
            return CheckResult(ok=True, latency_ms=_elapsed_ms(started))
    except socket.timeout:
        return CheckResult(ok=False, latency_ms=_elapsed_ms(started), error="tcp timeout")
    except OSError as exc:
        return CheckResult(ok=False, latency_ms=_elapsed_ms(started), error=f"tcp error: {exc.strerror or exc}")
    except Exception as exc:  # noqa: BLE001 - a probe must never kill the worker
        return CheckResult(ok=False, latency_ms=_elapsed_ms(started), error=f"{type(exc).__name__}: {exc}")


def run_check(endpoint: Endpoint) -> CheckResult:
    """Dispatch a probe by endpoint type."""
    if endpoint.kind in HTTP_KINDS:
        return check_http(endpoint)
    return check_tcp(endpoint)
