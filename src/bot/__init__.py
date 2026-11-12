"""Uptime Endpoint Monitoring Bot.

Periodically probes HTTP/TCP endpoints, records latency and status history in
SQLite, and notifies on down/recovered transitions.

Layout:
    config.py     typed YAML configuration (endpoints + thresholds)
    checks.py     HTTP and TCP probes, each returning a CheckResult
    storage.py    SQLite history and streak state
    notifiers.py  webhook / Telegram / Discord delivery (best-effort)
    main.py       worker loop, state machine, structured logging
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
