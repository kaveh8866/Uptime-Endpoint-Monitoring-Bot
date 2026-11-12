"""Alert delivery: generic webhook, Telegram and Discord.

Contract, borrowed from the backup project because it is the right one:
**notification is best-effort**. A slow or broken channel must never change the
outcome of a check or block the worker loop, so every sender swallows its own
errors, logs them, and returns a boolean for observability only.

Configuration is environment-only; nothing is read from the repository.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("bot.notify")

TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Channels:
    """Resolved channel configuration; empty fields mean "not configured"."""

    webhook_url: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    discord_url: str = ""

    @classmethod
    def from_env(cls) -> "Channels":
        return cls(
            webhook_url=os.environ.get("ALERT_WEBHOOK_URL", "").strip(),
            telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
            discord_url=os.environ.get("DISCORD_WEBHOOK_URL", "").strip(),
        )

    def active(self) -> list[str]:
        names = []
        if self.webhook_url:
            names.append("webhook")
        if self.telegram_token and self.telegram_chat_id:
            names.append("telegram")
        if self.discord_url:
            names.append("discord")
        return names


def _post_json(url: str, payload: dict, label: str) -> bool:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "cloudguard-uptime-bot/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        log.warning("alert channel failed", extra={"channel": label, "status": exc.code})
    except Exception as exc:  # noqa: BLE001 - never let alerting break monitoring
        log.warning("alert channel unreachable", extra={"channel": label, "error": str(exc)})
    return False


def _format_text(event: str, endpoint: str, detail: str) -> str:
    icon = "DOWN" if event == "down" else "RECOVERED" if event == "recovered" else event.upper()
    return f"[CloudGuard][{icon}] {endpoint}: {detail}"


def dispatch(event: str, endpoint: str, detail: str, channels: Channels) -> list[str]:
    """Send one alert to every configured channel; return the ones that worked."""
    if not channels.active():
        log.info("alert suppressed (no channel configured)", extra={"event": event, "endpoint": endpoint})
        return []

    text = _format_text(event, endpoint, detail)
    delivered: list[str] = []

    if channels.webhook_url and _post_json(
        channels.webhook_url,
        {"text": text, "event": event, "endpoint": endpoint, "detail": detail, "source": "uptime-bot"},
        "webhook",
    ):
        delivered.append("webhook")

    if channels.telegram_token and channels.telegram_chat_id:
        url = f"https://api.telegram.org/bot{channels.telegram_token}/sendMessage"
        if _post_json(url, {"chat_id": channels.telegram_chat_id, "text": text}, "telegram"):
            delivered.append("telegram")

    if channels.discord_url and _post_json(channels.discord_url, {"content": text}, "discord"):
        delivered.append("discord")

    log.info(
        "alert dispatched",
        extra={"event": event, "endpoint": endpoint, "delivered": delivered, "configured": channels.active()},
    )
    return delivered
