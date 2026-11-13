# Uptime Endpoint Monitoring Bot

![Status](https://img.shields.io/badge/status-portfolio%20%C2%B7%20cloudguard-2EA043)
![Stage](https://img.shields.io/badge/stage-certified%20100%2F100-2EA043)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/docker-compose%20v2-2496ED?logo=docker&logoColor=white)
![IaC](https://img.shields.io/badge/IaC-OpenTofu%20%C2%B7%20OCI-FFD814?logo=terraform&logoColor=black)
![License](https://img.shields.io/badge/license-MIT-blue)

A self-hosted uptime monitor that watches HTTP(S) and TCP endpoints, records
latency and status history, and only pages you when something is *actually*
down — not when one packet is late.

One container, one dependency (PyYAML), 18 MiB of RAM, no inbound port, no
database server to keep alive. The monitor cannot be the reason monitoring looks
healthy.

---

## Overview

Most uptime tools fail in one of two ways: they alert on every blip, or they need
a stack of their own (database, queue, dashboard) that can fail independently of
the thing being watched. This bot takes the opposite position:

- **Streaks, not samples.** `FAILURE_THRESHOLD` consecutive failures are required
  before an endpoint is declared DOWN; a single timeout is noise.
- **Transitions, not polls.** Only `down` and `recovered` dispatch a notification.
  A five-hour outage produces two messages, not six hundred.
- **Outbound only.** The process opens sockets and posts JSON. It never listens,
  so there is no attack surface beyond SSH on the host.
- **State is local and boring.** SQLite on a volume, two tables, no daemon.

## Features

| Feature | Detail |
| --- | --- |
| HTTP / HTTPS probes | `GET`, `HEAD`, custom headers, expected status sets, redirect-aware |
| TCP probes | Port reachability for databases, DNS, SMTP, brokers |
| Per-endpoint scheduling | Independent intervals from 5 s to hours, no drift between fast and slow checks |
| Latency tracking | Per-probe milliseconds persisted to SQLite, plus warn/critical bands |
| State machine | `unknown → up` silently; `down` / `recovered` only on threshold crossings |
| Alert channels | Generic JSON webhook, Telegram, Discord — all optional, all independent |
| Best-effort delivery | A dead channel logs a warning and never blocks or alters a check |
| Hardened container | Read-only rootfs, non-root uid 999, 128 MB / 0.2 CPU caps, `no-new-privileges`, rotated logs |
| Structured logs | One JSON object per line — ingestible by Loki, CloudWatch, or `jq` |
| Bounded history | `HISTORY_RETENTION_DAYS` prune runs hourly; state rows are kept |
| Portable by design | Docker + outbound sockets; IaC in OpenTofu with OCI, AWS and GCP mappings |
| Zero-leak repo | `.env`, `*.db`, keys, state and local configs are gitignored and audited |

---

## Architecture

```text
                        ┌──────────────────────────────────────────────┐
                        │              Uptime Bot Worker               │
                        │   scheduler · state machine · JSON logging   │
                        └───────┬──────────────────────────────┬───────┘
                                │                              │
              (periodic HTTP/TCP check)              (state / latency log)
                                │                              │
                                ▼                              ▼
                    ┌───────────────────────┐      ┌────────────────────────┐
                    │   Target Endpoints    │      │  SQLite  /data/*.db    │
                    │  http(s): url + code  │      │  checks (history)      │
                    │  tcp: host + port     │      │  endpoint_state        │
                    └───────────┬───────────┘      └───────────┬────────────┘
                                │                              │
                    (status code, RTT, error)        (streak reached threshold)
                                │                              │
                                └──────────────┬───────────────┘
                                               ▼
                                 ┌───────────────────────────┐
                                 │  Alert Dispatcher         │
                                 │  down | recovered only    │
                                 └─────────────┬─────────────┘
                                               │
                        ┌──────────────────────┼──────────────────────┐
                        ▼                      ▼                      ▼
                  Telegram Bot           Discord Webhook       Generic JSON
                  (sendMessage)          (content POST)        (Slack, n8n,
                                                               Grafana, custom)
```

Host layout — the bot is the only service, and `/data` is its only writable path:

```text
docker/docker-compose.yml
└── service: uptime-bot   (128 MB, 0.2 CPU, read-only rootfs, tmpfs /tmp)
    ├── volume  uptime-data:/data        → SQLite state + history
    └── bind    ../configs:/app/configs:ro → endpoints.yaml, thresholds.yaml
```

---

## Quick start

```bash
git clone https://github.com/kaveh8866/Uptime-Endpoint-Monitoring-Bot.git
cd Uptime-Endpoint-Monitoring-Bot

cp .env.example .env          # blank the channels you do not use
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f uptime-bot
```

First cycle, one JSON line per endpoint:

```json
{"ts":"2026-11-12T22:19:36Z","level":"info","logger":"bot.monitor","message":"check completed","endpoint":"Public API Edge","status_code":200,"latency_ms":43.95,"error":null,"state":"up","transition":null,"detail":"200 in 44ms"}
```

Without Docker (Python 3.12+ and `pip install -r requirements.txt`):

```bash
ENDPOINTS_FILE=configs/endpoints.yaml THRESHOLDS_FILE=configs/thresholds.yaml \
STATE_DB_PATH=./uptime.db PYTHONPATH=src python -m bot.main
```

### Verified live deployment

The reference deployment runs on OCI in `eu-frankfurt-1`, Always Free envelope:

| | |
| --- | --- |
| Host | `129.159.30.105` (`VM.Standard.A1.Flex`, 1 OCPU / 1 GB, Ubuntu 24.04 aarch64) |
| Access | `ssh ubuntu@129.159.30.105` — security list ingress is 22/tcp only |
| Runtime | Docker 29.9.3, `cg-uptime` at **19.82 MiB / 128 MiB**, 0.01 % CPU |
| Probes | `example.com` 97 ms GET / 69 ms HEAD, `1.1.1.1:53` 1.1 ms — from Frankfurt |
| Alerts | live drill produced one `down` and one `recovered`, both `delivered:["webhook"]` |

Provisioning detail worth knowing: AD-1 returned `Out of host capacity`; the same
plan succeeded in AD-2 with no change and no paid shape. Full transcript:
[`TEST_REPORT.md`](TEST_REPORT.md) §8.

**Teardown performed (Stage 6):** the instance, VCN and every network resource
were destroyed after verification — `Destroy complete! Resources: 6 destroyed`,
`tofu state list` empty, and the provider reports the instance `TERMINATED`.
Burn rate is back to **$0**. Re-deploy with `cd terraform && tofu apply` followed
by the compose command above; the evidence in this section is the record of the
run that was, not a claim about a live host.

Before deploying for real, work through [`MANUAL_PREREQS.md`](MANUAL_PREREQS.md)
(credentials, channels, endpoints, `.env`, cloud access). To hand the repository
to a coding agent, point it at [`AGENT_GUIDE.md`](AGENT_GUIDE.md) — phased
commands with explicit stop conditions.

---

## Configuration

### `configs/endpoints.yaml` — what to watch

```yaml
defaults:
  interval_seconds: 60      # per-endpoint cadence (minimum 5)
  timeout_seconds: 5        # a slow answer is a failed answer
  expected_status: [200]    # anything else counts as unhealthy

endpoints:
  - name: Public API Edge       # unique — this is the alert's identity
    type: http
    url: https://api.example.com/health
    interval_seconds: 30
    expected_status: [200, 204]
    headers:
      Accept: application/json

  - name: Docs (HEAD)
    type: http
    url: https://docs.example.com/
    method: HEAD
    interval_seconds: 300

  - name: Primary Postgres
    type: tcp
    host: db.example.com
    port: 5432
    timeout_seconds: 3
```

| Key | Applies to | Notes |
| --- | --- | --- |
| `name` | all | Must be unique; state is keyed by it |
| `type` | all | `http` or `tcp` |
| `url` | http | Scheme decides HTTP vs HTTPS |
| `method` | http | Default `GET`; `HEAD` is cheaper for liveness |
| `headers` | http | Sent verbatim (auth headers belong in `.env`, not here) |
| `host` / `port` | tcp | Success means the TCP handshake completes |
| `expected_status` | http | List of acceptable codes; redirects are followed |
| `interval_seconds` | all | Floor of 5 s; per-endpoint override |
| `timeout_seconds` | all | Per-probe budget |

Probes run **from inside the container**: `127.0.0.1` means the container itself,
so a host-local service needs the host IP or `host.docker.internal`.

### `configs/thresholds.yaml` — when a wobble becomes an incident

```yaml
failure_threshold: 3          # consecutive failures before DOWN
recovery_threshold: 1         # consecutive successes before RECOVERED
latency_warn_ms: 800
latency_critical_ms: 2000
```

Override at run time with `FAILURE_THRESHOLD` / `RECOVERY_THRESHOLD`. Never ship
`FAILURE_THRESHOLD=1`: one dropped packet would page someone for a healthy
service.

### Notification triggers

Channels are environment-only. Empty means "off":

```bash
ALERT_WEBHOOK_URL=https://hooks.example.com/services/…     # any JSON endpoint
TELEGRAM_BOT_TOKEN=…   TELEGRAM_CHAT_ID=…
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/…
```

The bot POSTs:

```json
{"text":"[CloudGuard][DOWN] Public API Edge: status 503","event":"down",
 "endpoint":"Public API Edge","detail":"status 503","source":"uptime-bot"}
```

Telegram uses `sendMessage`, Discord uses `{"content": …}`. Delivery is
best-effort: failures are logged as `alert channel unreachable` and the loop
keeps probing.

**Test a trigger without waiting for a real outage** — point one endpoint at a
closed port, run with a 2-failure threshold against a local receiver, then start
a listener to produce the recovery:

```bash
cat > configs/drill.local.yaml <<'YAML'
defaults: {interval_seconds: 5, timeout_seconds: 2}
endpoints:
  - {name: Drill Target, type: tcp, host: 127.0.0.1, port: 8089}
YAML

docker compose --env-file .env -f docker/docker-compose.yml run --rm \
  -e ENDPOINTS_FILE=/app/configs/drill.local.yaml \
  -e FAILURE_THRESHOLD=2 -e RECOVERY_THRESHOLD=1 \
  -e ALERT_WEBHOOK_URL=http://host.docker.internal:8912/alert \
  uptime-bot sh -c '
    python -m bot.main > /tmp/drill.log 2>&1 & bot=$!
    sleep 14; python -m http.server 8089 --bind 127.0.0.1 >/dev/null 2>&1 & web=$!
    sleep 12; kill -TERM $bot $web; wait $bot
    grep -E "\"transition\"|alert dispatched" /tmp/drill.log'
```

Expected: one `"transition":"down"`, one `"transition":"recovered"`, and two
`"delivered":["webhook"]`. Full walkthrough with a receiver that returns 200:
[`AGENT_GUIDE.md`](AGENT_GUIDE.md) Phase 4–5. Measured results:
[`TEST_REPORT.md`](TEST_REPORT.md).

---

## Cloud-agnostic portability

The workload is provider-neutral: a Docker image that opens outbound sockets and
posts JSON. Nothing in `src/`, `docker/` or `configs/` knows what a cloud is.
Provider specifics live only in `terraform/modules/*`, behind an interface of
provider-neutral variable names (`region`, `vcn_cidr`, `subnet_cidr`,
`ssh_allowed_cidr`, `instance_shape`, `ssh_public_key`).

> **Certification scope.** Production verification and testing certification are
> **performed strictly on Oracle Cloud Infrastructure (OCI)** using Always Free /
> trial credits. AWS and GCP are supported by design and documented below, but
> they have **not** been deployed or certified — do not describe this project as
> "AWS tested" or "GCP tested".

| Concept | OCI (implemented) | AWS | GCP |
| --- | --- | --- | --- |
| Network | `oci_core_vcn` | `aws_vpc` | `google_compute_network` |
| Gateway | `oci_core_internet_gateway` | `aws_internet_gateway` | implicit on a default-route VPC |
| Routes | `oci_core_route_table` | `aws_route_table` + `aws_route` | `google_compute_route` |
| Firewall | `oci_core_security_list` | `aws_security_group` + rules | `google_compute_firewall` |
| Subnet | `oci_core_subnet` | `aws_subnet` | `google_compute_subnetwork` |
| Instance | `oci_core_instance` (A1.Flex) | `aws_instance` (`t4g.small` / `t3.micro`) | `google_compute_instance` (`e2-micro`) |
| Bootstrap | `metadata.user_data` | `user_data` | `metadata_startup_script` |
| Public IP | VNIC `public_ip_address` | `aws_eip` / instance attribute | `access_config` `nat_ip` |

### Migrating to AWS

1. Copy `modules/network` → `modules/network-aws`, swap the five resources for
   `aws_vpc`, `aws_internet_gateway`, `aws_route_table`/`aws_route`,
   `aws_security_group` (+ ingress 22 from `ssh_allowed_cidr`, egress all) and
   `aws_subnet`. Keep the same variable names and outputs.
2. Copy `modules/compute` → `modules/compute-aws`: `aws_instance` with
   `ami` (Ubuntu 24.04 arm64), `instance_type = "t4g.small"` or `"t3.micro"`,
   `associate_public_ip_address = true`, and the same base64 cloud-init in
   `user_data`.
3. Point `main.tf` at the new module sources. `docker/`, `src/` and `configs/`
   are unchanged — that is the test of the port.
4. Budget for the **public IPv4 charge** (~$3.65/mo) and remember the current
   free tier is credit-based ($200 / 6 months for new accounts), not 750 hours
   forever.

### Migrating to GCP

1. `google_compute_network` + `google_compute_subnetwork` + `google_compute_firewall`
   (allow `tcp:22` from `ssh_allowed_cidr`, egress all) replace the VCN block.
2. `google_compute_instance` with `machine_type = "e2-micro"`,
   `network_interface { access_config {} }` for the public IP, and
   `metadata_startup_script` for the same cloud-init body.
3. Keep the instance in `us-central1`, `us-west1` or `us-east1` to stay inside
   the free tier; outside those regions e2-micro bills (~$6.11/mo).
4. Egress beyond 1 GB/month is billed — this workload uses a fraction of that.

Both migrations are mechanical because the security contract is identical
everywhere: **SSH in, everything out**.

---

## 💰 Cross-Cloud Cost & Benchmark Estimation

Footprint being priced — the measured Stage 4 workload, not a guess:

| Parameter | Measured / chosen value |
| --- | --- |
| vCPU | 1 (container capped at 0.2 CPU; observed 0.01 %) |
| RAM | 1 GB instance (container observed **18.19 MiB**) |
| Block storage | 50 GB boot volume |
| Check frequency | 5 endpoints; 30 s / 60 s / 120 s / 300 s → ~205 k checks/month |
| Network egress | ~0.4 GB/month (1–2 KB per check + alerts) |
| Disk growth | ~50 MB/month of SQLite history at 14-day retention |

List/on-demand prices, US regions (us-east-1 / us-central1 / eu-frankfurt-1),
November 2026. Vendor pricing changes; re-check with each provider's calculator
before committing budget.

| Cost line | OCI (Always Free) | OCI (Trial, $300 / 30 d) | AWS (`t3.micro`) | GCP (`e2-micro`) |
| --- | --- | --- | --- | --- |
| Compute, 730 h | **$0** (1,500 A1 OCPU-h + 9,000 GB-h/mo) | $0 until credit ends | $0 during credit ($200/6 mo) → **$7.59** | $0 in free-tier region → **$6.11** |
| Block storage, 50 GB | **$0** (200 GB included) | $0 | 30 GB free, then ≈**$1.60** (20 GB × $0.08) | 30 GB free, then ≈**$0.80** (20 GB × $0.04) |
| Egress, ≤1 GB/mo | **$0** (10 TB included) | $0 | **$0** (100 GB free, then $0.09/GB) | **$0** (1 GB free, then ≈$0.12/GB) |
| Public IPv4 | **$0** (included) | $0 | ≈**$3.65** ($0.005/h) | $0 on free-tier e2-micro |
| Monitoring / metrics | $0 (self-hosted, logs on disk) | $0 | $0 (CloudWatch optional, extra) | $0 (Cloud Monitoring optional, extra) |
| **Effective monthly total** | **$0** | **$0** | **≈$12.84** after credits (7.59 + 1.60 + 3.65) | **≈$6.91** after free tier (6.11 + 0.80) |
| Free-tier duration | Always Free | 30 days or credit exhaustion | $200 credit / 6 months (new accounts) | Always Free (eligible region) |

**Benchmark notes**

- Latency figures come from the local verification run (44 ms to `example.com`,
  8 ms to `1.1.1.1:53`); a cloud host adds its own RTT to each target, so expect
  numbers dominated by geography, not by the bot.
- CPU is effectively idle: at 205 k checks/month the container spent 0.01 % of a
  core, so even the smallest shape is oversized. Memory is the only real
  constraint, and 18 MiB leaves room for a 5× larger watch list.
- The metric that actually differs between providers is **egress allowance** and
  **IPv4 billing**, not compute. OCI's 10 TB allowance is why this workload is
  free there; on AWS the public address alone costs more than the compute.
- Trial credits are not a free tier: when they expire, anything outside the
  Always Free envelope starts billing. Destroy or right-size before that date.

---

## Security posture

| Control | Implementation |
| --- | --- |
| No inbound surface | No listening port; only SSH (22) is open on the host |
| Container hardening | `read_only: true`, tmpfs `/tmp`, non-root uid 999, `no-new-privileges`, `pids_limit`, memory/CPU caps |
| Secret handling | `.env` only, gitignored; `.env.example` ships placeholders, never values |
| Least privilege | Alerts are outbound HTTPS; the bot holds no cloud credential |
| State protection | SQLite on a dedicated volume, gitignored; history pruned automatically |
| Supply chain | One pinned dependency (`PyYAML==6.0.2`), no build toolchain in the image |
| Audit trail | JSON logs with endpoint, state, transition and delivery result |

Full scan results and sign-off: [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md).

```bash
# prove the boundary yourself
git check-ignore -v .env
git grep -nE "(TELEGRAM_BOT_TOKEN|DISCORD_WEBHOOK_URL)=.+" -- . ':!.env.example'
```

---

## Project status

| Stage | Scope | State |
| --- | --- | --- |
| 1 | Scaffold, worker loop, probes, SQLite state, notifiers, hardened compose | ✅ |
| 2 | OpenTofu: OCI VCN, A1.Flex compute, cloud-init bootstrap | ✅ validated (`tofu validate`, 4/4 posture tests) |
| 3 | Manual prerequisites + agent playbook | ✅ |
| 4 | Local drill: forced outage → alert → recovery, latency history | ✅ webhook path ([report](TEST_REPORT.md)) |
| 5 | Live OCI deployment (apply → SSH → compose up → alert on host) | ✅ verified on OCI (`129.159.30.105`) |
| 6 | Teardown audit | ✅ zero resources ([report](TEARDOWN_REPORT.md)) |

The honest summary: the stack was verified locally (Stage 4) **and** deployed to a
live OCI Ampere instance (Stage 5), where it probes public endpoints, writes
latency history and delivered `down` → `recovered` alerts to a webhook receiver.
What remains unproven is durability — the host has been up for minutes, not days
— and Telegram/Discord delivery, which needs real channel credentials.

## Repository layout

```text
src/bot/            config · checks · storage · notifiers · main
configs/            endpoints.yaml, thresholds.yaml
docker/             docker-compose.yml (hardened stack)
terraform/          OpenTofu: modules/network, modules/compute, tests, cloud-init
Dockerfile          python:3.12-slim, non-root, no build toolchain
AGENT_GUIDE.md      phased playbook for AI coding agents
MANUAL_PREREQS.md   operator checklist and credential recipes
TEST_REPORT.md      Stage 4 measured results
TEARDOWN_REPORT.md  Stage 6 zero-resource audit
SECURITY_AUDIT.md   Stage 7 leak scan and sign-off
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Mostafa Kaveh.
