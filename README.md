# Uptime Endpoint Monitoring Bot

![Status](https://img.shields.io/badge/stage-1%20%C2%B7%20scaffold-2EA043)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![Dependencies](https://img.shields.io/badge/runtime%20deps-1%20(PyYAML)-blue)
![Cost](https://img.shields.io/badge/cost-%240.00%20%2F%20month-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

A small, self-hosted uptime monitor: it probes HTTP and TCP endpoints on their
own schedules, records status codes and latency in SQLite, and tells you — over a
webhook, Telegram or Discord — when something goes **down** and when it comes
**back**. No SaaS account, no agent, no external database, one dependency.

> Part of the **CloudGuard** portfolio: every project runs on free-tier
> infrastructure, ships as a container, and can be handed to another agent
> without an environment lock.

---

## Why this exists

Uptime tools fail in two directions. Hosted ones are excellent until you need
them to watch the wrong thing, or until the alert you care about costs a plan
upgrade. Self-hosted ones usually assume a whole observability stack — Prometheus,
Alertmanager, Grafana — to answer one question: *is this thing answering requests
right now?*

This bot answers that question with ~450 lines of Python, a YAML file and a
SQLite file, and it is explicit about the failure semantics that actually matter:

- **Streaks, not blips.** Three consecutive failures is an incident; one dropped
  packet is not. The first success afterwards is a recovery.
- **Honest state.** `unknown` is internal. A restart discovering a healthy
  service is not a "recovery", so it does not page anyone.
- **Alerting cannot break monitoring.** Every channel is best-effort: a dead
  webhook logs a warning and the loop keeps probing.

---

## Architecture

```text
                    every endpoint on its own schedule
                                │
        ┌───────────────────────┴───────────────────────┐
        │             uptime-bot (container)            │
        │                                               │
        │  config.py ──► checks.py ──► storage.py       │
        │  endpoints.yaml   HTTP/TCP      SQLite        │
        │  thresholds.yaml  latency+code  /data/uptime.db│
        │                       │                       │
        │                       ▼                       │
        │              state machine (streaks)          │
        │                       │                       │
        │                       ▼                       │
        │                 notifiers.py ─────────────┐   │
        └───────────────────────────────────────────┼───┘
                                                    │ HTTPS (outbound only)
                        ┌───────────────────────────┴──────────┐
                        ▼                    ▼                 ▼
                   Webhook (JSON)       Telegram           Discord
```

| Module | Responsibility |
| ------ | -------------- |
| `src/bot/config.py` | Typed YAML loading, env overrides, validation with actionable errors |
| `src/bot/checks.py` | HTTP(S) and TCP probes; timeouts and refused connections are results, not exceptions |
| `src/bot/storage.py` | SQLite history (`checks`) and streak state (`endpoint_state`), WAL mode |
| `src/bot/notifiers.py` | Webhook / Telegram / Discord delivery, best-effort by contract |
| `src/bot/main.py` | Per-endpoint scheduling, state machine, JSON logging, graceful shutdown |
| `configs/` | `endpoints.yaml` (watch list) and `thresholds.yaml` (when a wobble is an incident) |
| `docker/` | Hardened compose stack: read-only rootfs, 128 MB / 0.2 CPU, log rotation |
| `terraform/` | Reserved for Stage 2 (OCI Always Free compute + SSH-only security list) |

---

## Quick start

```bash
git clone https://github.com/kaveh8866/Uptime-Endpoint-Monitoring-Bot.git
cd Uptime-Endpoint-Monitoring-Bot

cp .env.example .env        # leave channels empty for a log-only run
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f uptime-bot
```

Read the first cycle as JSON, one line per probe:

```json
{"ts":"2026-11-12T22:05:01Z","level":"info","logger":"bot.monitor","message":"check completed",
 "endpoint":"CloudGuard Gateway","status_code":200,"latency_ms":3.41,"state":"up","transition":null}
```

Without Docker (Python 3.12+ and `pip install -r requirements.txt`):

```bash
ENDPOINTS_FILE=configs/endpoints.yaml THRESHOLDS_FILE=configs/thresholds.yaml \
STATE_DB_PATH=./uptime.db PYTHONPATH=src python -m bot.main
```

---

## Configuration

**`configs/endpoints.yaml`** — what to watch:

```yaml
defaults:
  interval_seconds: 60
  timeout_seconds: 5
  expected_status: [200]

endpoints:
  - name: CloudGuard Gateway
    type: http
    url: http://127.0.0.1:8080/health
    interval_seconds: 30

  - name: Postgres Port
    type: tcp
    host: 127.0.0.1
    port: 5432
    timeout_seconds: 3
```

**`configs/thresholds.yaml`** — when to alert:

| Key | Default | Meaning |
| --- | ------- | ------- |
| `failure_threshold` | `3` | Consecutive failures before **DOWN** |
| `recovery_threshold` | `1` | Consecutive successes before **RECOVERED** |
| `latency_warn_ms` | `800` | Logged as a warning signal |
| `latency_critical_ms` | `2000` | Log level raised to warning, visible in `docker logs` |

**Environment** (`.env`, never committed):

| Variable | Purpose |
| -------- | ------- |
| `ALERT_WEBHOOK_URL` | Any JSON endpoint (Slack, Teams, n8n, Grafana, custom) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram delivery |
| `DISCORD_WEBHOOK_URL` | Discord webhook delivery |
| `FAILURE_THRESHOLD`, `RECOVERY_THRESHOLD` | Override the YAML counts |
| `HISTORY_RETENTION_DAYS` | History pruning window (default 14) |
| `LOG_LEVEL` | `INFO` (default) / `DEBUG` / `WARNING` |

With no channel configured the bot is deliberately quiet: it logs
`alert suppressed (no channel configured)` and keeps recording history, so you
can run it for days before deciding where alerts should go.

---

## Hardening

| Control | Implementation |
| ------- | -------------- |
| Least privilege | Container runs as the unprivileged `bot` user; `no-new-privileges` |
| Immutable rootfs | `read_only: true`, with `/data` as the only writable mount and `/tmp` as a 16 MB tmpfs |
| Resource ceiling | `mem_limit: 128m`, `cpus: 0.20`, `pids_limit: 128` — fits a 1 OCPU Always Free ARM VM |
| No inbound surface | The bot only makes outbound connections; nothing is published |
| Secret hygiene | `.env`, `*.db`, keys and local configs are gitignored; `.env.example` holds placeholders only |
| Log hygiene | JSON lines with `json-file` rotation at 10 MB × 2 |

---

## Status

| Stage | Scope | State |
| ----- | ----- | ----- |
| **1** | Scaffold, worker loop, probes, SQLite state, notifiers, hardened compose | ✅ |
| **2** | OpenTofu: OCI VCN, A1.Flex compute, cloud-init bootstrap | ⬜ |
| **3** | Manual prerequisites + agent playbook | ⬜ |
| **4** | Live drill: forced outage → alert → recovery, latency history | ⬜ |
| **5** | Optional read-only status page and `/metrics` for Prometheus | ⬜ |

---

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Mostafa Kaveh.
