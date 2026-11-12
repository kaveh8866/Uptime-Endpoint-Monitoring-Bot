"""Configuration loading for the uptime bot.

Two files, deliberately separate:

* ``configs/endpoints.yaml``  — what to watch and how often
* ``configs/thresholds.yaml`` — when a wobble becomes an incident

Environment variables override the file locations (``ENDPOINTS_FILE``,
``THRESHOLDS_FILE``) and the global defaults (``CHECK_INTERVAL_SECONDS``,
``HTTP_TIMEOUT_SECONDS``), so the same image runs unchanged on a laptop and on
an OCI Always Free host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ENDPOINTS_FILE = "/app/configs/endpoints.yaml"
DEFAULT_THRESHOLDS_FILE = "/app/configs/thresholds.yaml"

HTTP_KINDS = {"http", "https"}
TCP_KINDS = {"tcp"}


class ConfigError(RuntimeError):
    """Raised when a configuration file is missing or malformed."""


@dataclass(frozen=True)
class Endpoint:
    """A single thing to check."""

    name: str
    kind: str
    interval_seconds: int
    timeout_seconds: float
    url: str | None = None
    host: str | None = None
    port: int | None = None
    method: str = "GET"
    expected_status: tuple[int, ...] = (200,)
    headers: dict[str, str] = field(default_factory=dict)

    def target(self) -> str:
        """Human-readable target for logs and alerts."""
        if self.kind in HTTP_KINDS:
            return self.url or ""
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class Thresholds:
    """When to declare an endpoint down, and when to declare it recovered."""

    failure_threshold: int
    recovery_threshold: int
    latency_warn_ms: int
    latency_critical_ms: int


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - configuration error path
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - configuration error path
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return loaded


def load_thresholds(path: str | Path | None = None) -> Thresholds:
    """Load alert thresholds, allowing env overrides for the two counts."""
    source = Path(path or os.environ.get("THRESHOLDS_FILE", DEFAULT_THRESHOLDS_FILE))
    data = _read_yaml(source)

    failure = _env_int("FAILURE_THRESHOLD", int(data.get("failure_threshold", 3)))
    recovery = _env_int("RECOVERY_THRESHOLD", int(data.get("recovery_threshold", 1)))

    if failure < 1 or recovery < 1:
        raise ConfigError("failure_threshold and recovery_threshold must be >= 1")

    return Thresholds(
        failure_threshold=failure,
        recovery_threshold=recovery,
        latency_warn_ms=int(data.get("latency_warn_ms", 800)),
        latency_critical_ms=int(data.get("latency_critical_ms", 2000)),
    )


def _parse_endpoint(raw: Any, defaults: dict[str, Any], index: int) -> Endpoint:
    if not isinstance(raw, dict):
        raise ConfigError(f"endpoint #{index} must be a mapping")

    name = str(raw.get("name") or "").strip()
    kind = str(raw.get("type") or defaults.get("type") or "http").strip().lower()
    if not name:
        raise ConfigError(f"endpoint #{index} has no name")
    if kind not in HTTP_KINDS | TCP_KINDS:
        raise ConfigError(f"endpoint {name!r} has unsupported type {kind!r}")

    interval = int(raw.get("interval_seconds") or _env_int(
        "CHECK_INTERVAL_SECONDS", int(defaults.get("interval_seconds", 60))
    ))
    timeout = float(raw.get("timeout_seconds") or _env_float(
        "HTTP_TIMEOUT_SECONDS", float(defaults.get("timeout_seconds", 5))
    ))
    if interval < 5:
        raise ConfigError(f"endpoint {name!r} interval_seconds must be >= 5")
    if timeout <= 0:
        raise ConfigError(f"endpoint {name!r} timeout_seconds must be > 0")

    expected = raw.get("expected_status", defaults.get("expected_status", [200]))
    if isinstance(expected, int):
        expected = [expected]
    if not isinstance(expected, list) or not expected:
        raise ConfigError(f"endpoint {name!r} expected_status must be a list of codes")

    headers = raw.get("headers") or {}
    if not isinstance(headers, dict):
        raise ConfigError(f"endpoint {name!r} headers must be a mapping")

    if kind in HTTP_KINDS:
        url = str(raw.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"endpoint {name!r} needs an http(s) url")
        return Endpoint(
            name=name,
            kind=kind,
            interval_seconds=interval,
            timeout_seconds=timeout,
            url=url,
            method=str(raw.get("method", "GET")).upper(),
            expected_status=tuple(int(code) for code in expected),
            headers={str(k): str(v) for k, v in headers.items()},
        )

    host = str(raw.get("host") or "").strip()
    port = raw.get("port")
    if not host or port is None:
        raise ConfigError(f"endpoint {name!r} needs host and port for a tcp check")
    return Endpoint(
        name=name,
        kind=kind,
        interval_seconds=interval,
        timeout_seconds=timeout,
        host=host,
        port=int(port),
    )


def load_endpoints(path: str | Path | None = None) -> list[Endpoint]:
    """Load the watch list, applying ``defaults`` to every entry."""
    source = Path(path or os.environ.get("ENDPOINTS_FILE", DEFAULT_ENDPOINTS_FILE))
    data = _read_yaml(source)

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("endpoints.yaml 'defaults' must be a mapping")

    raw_endpoints = data.get("endpoints")
    if not isinstance(raw_endpoints, list) or not raw_endpoints:
        raise ConfigError("endpoints.yaml must declare a non-empty 'endpoints' list")

    endpoints = [_parse_endpoint(raw, defaults, index) for index, raw in enumerate(raw_endpoints, start=1)]

    names = [endpoint.name for endpoint in endpoints]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"duplicate endpoint names: {', '.join(duplicates)}")

    return endpoints
