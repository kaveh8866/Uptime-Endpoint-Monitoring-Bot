# TEST_REPORT.md — local verification (Stage 4)

Recorded on 2026-11-12, Docker 29.8.1 on Windows, Git Bash, project commit
`c9dcbe6`. Reproduce with the commands below; nothing here needs cloud
credentials.

> **Scope of certification:** §1–§7 are local results on one developer machine.
> §8 is a **live OCI deployment** (Ampere A1.Flex, Always Free envelope) and is
> the only environment certified for hosting — see `MANUAL_PREREQS.md` §8. AWS
> and GCP are portable by design but remain untested.

---

## 1. Stack startup

```bash
cp .env.example .env
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml ps
```

| Check | Result |
| --- | --- |
| Container | `cg-uptime` — `Up`, image `cloudguard/uptime-bot:local` |
| Initialization line | `"message":"uptime bot starting","endpoint":"5 endpoint(s)","state":"failure>=3, recovery>=1"` |
| Restart loop | none (single `Started`, `Exited (0)` only after an explicit `stop`) |
| Memory | **18.19 MiB / 128 MiB** |
| CPU | 0.01 % |

## 2. Endpoint configuration parsing

`configs/endpoints.yaml` shipped with five endpoints covering both probe types:

| Endpoint | Type | Target | Interval | Result in run |
| --- | --- | --- | --- | --- |
| CloudGuard Gateway | http | `http://127.0.0.1:8080/health` | 30 s | refused (nothing listening) — recorded as a result |
| Public API Edge | http | `https://example.com/` | 120 s | `200` |
| Docs (HEAD) | http | `https://example.com/` | 300 s | `200` |
| Postgres Port | tcp | `127.0.0.1:5432` | 60 s | refused — recorded as a result |
| Public DNS over TCP | tcp | `1.1.1.1:53` | 300 s | ok |

The bot logged the parsed count at startup (`5 endpoint(s)`) and then produced
exactly one record per endpoint — so configuration is read, validated and mapped
to probes, not merely present. Refused connections arrive as `status_code:null`
with an `error` string and `"level":"warning"`, which is the intended behaviour:
a dead target is data, not a crash.

**Interval, expected status, headers and HEAD method** are all exercised by this
file: the two `example.com` probes use different intervals (120 s / 300 s) and
methods, and both return `200`.

## 3. Worker loop and latency logging

Two consecutive cycles were observed in ~40 s (5 endpoints, all due at start,
`CloudGuard Gateway` re-probing at 30 s):

```json
{"level":"info","logger":"bot.monitor","message":"check completed","endpoint":"Public API Edge","status_code":200,"latency_ms":43.95,"state":"up","transition":null,"detail":"200 in 44ms"}
{"level":"warning","logger":"bot.monitor","message":"check completed","endpoint":"Postgres Port (backup host)","status_code":null,"latency_ms":0.11,"error":"tcp error: Connection refused","state":"unknown","transition":null}
{"level":"info","logger":"bot.monitor","message":"check completed","endpoint":"Public DNS over TCP","status_code":null,"latency_ms":8.11,"state":"up","transition":null,"detail":"ok in 8ms"}
```

12 `check completed` lines in the run; history after both runs:

```text
rows: {'checks': 22, 'endpoint_state': 6}
CloudGuard Gateway                     n=4  ok=0  avg=19.0 ms
Docs (HEAD, redirects allowed as 200)  n=2  ok=2  avg=34.3 ms
Drill Target                           n=10 ok=4  avg=1.4 ms
Postgres Port (backup host)            n=2  ok=0  avg=0.2 ms
Public API Edge                        n=2  ok=2  avg=45.6 ms
Public DNS over TCP                    n=2  ok=2  avg=7.8 ms
```

Latency is measured per probe (`latency_ms`, sub-millisecond resolution),
persisted, and summarised by SQL. `endpoint_state` held 5 rows after the baseline
run and 6 after the drill — one row per endpoint, keyed by name.

## 4. Alert triggering: outage → down → recovered

A one-endpoint drill config (`configs/drill.local.yaml`, gitignored) pointed at a
closed port; a listener was started mid-run so the endpoint recovered without
touching the service. Thresholds: `FAILURE_THRESHOLD=2`,
`RECOVERY_THRESHOLD=1`. A local HTTP receiver on port 8912 stood in for the
production channel.

```bash
docker compose --env-file .env -f docker/docker-compose.yml run --rm \
  -e ENDPOINTS_FILE=/app/configs/drill.local.yaml \
  -e FAILURE_THRESHOLD=2 -e RECOVERY_THRESHOLD=1 \
  -e ALERT_WEBHOOK_URL=http://host.docker.internal:8912/alert \
  uptime-bot sh -c '...'
```

Bot side:

```json
{"message":"check completed","endpoint":"Drill Target","error":"tcp error: Connection refused","state":"down","transition":"down"}
{"message":"alert dispatched","event":"down","endpoint":"Drill Target","delivered":["webhook"],"configured":["webhook"]}
{"message":"check completed","endpoint":"Drill Target","state":"up","transition":"recovered","detail":"ok in 0ms"}
{"message":"alert dispatched","event":"recovered","endpoint":"Drill Target","delivered":["webhook"],"configured":["webhook"]}
```

Receiver side — exactly two POSTs, correct payloads, no duplicates:

```text
POST /alert {"text": "[CloudGuard][DOWN] Drill Target: tcp error: Connection refused", "event": "down", "endpoint": "Drill Target", "detail": "tcp error: Connection refused", "source": "uptime-bot"}
POST /alert {"text": "[CloudGuard][RECOVERED] Drill Target: ok in 0ms", "event": "recovered", "endpoint": "Drill Target", "detail": "ok in 0ms", "source": "uptime-bot"}
```

| Assertion | Result |
| --- | --- |
| One `transition:"down"` | ✅ (1) |
| One `transition:"recovered"` | ✅ (1) |
| No duplicate alerts for the sustained outage | ✅ (3 failed probes, 1 alert) |
| Alert delivered to a real HTTP endpoint | ✅ `delivered:["webhook"]`, receiver 200 |
| State persisted after the drill | ✅ `('Drill Target','up',0,2)` |
| Service resumes probing after a restart | ✅ 12 checks, 22 history rows |

Raw drill history: `(0, refused), (0, refused), (0, refused), (1, ok), (1, ok)`
per run — failures accumulate until the threshold, then one alert; recovery
alerts once.

## 5. Findings

1. **`.env.example` channels are non-empty placeholders**, so copying the file
   untouched makes the bot report `"configured":["webhook","telegram","discord"]`
   and attempt delivery to `example.invalid` on a real incident. Blank the
   channels you do not use. The template keeps non-empty values on purpose (the
   portability audit requires a fully populated example), so this is documented
   rather than "fixed" — see `MANUAL_PREREQS.md` §3d.
2. **Local targets are container-local.** `127.0.0.1:8080` and `127.0.0.1:5432`
   are refused because nothing listens *inside* the container; that is correct
   behaviour for the sample file, not a defect.
3. **A stale receiver silently invalidated a run.** The first drill reported
   `delivered:["webhook"]` while the new receiver logged nothing: an earlier test
   process still held port 8899, so the bot was right and the harness was wrong.
   Verify the receiver port is free (or preflight it) before reading a delivery
   result.
4. **Windows quirks cost real debugging time** and are now in the docs:
   `MSYS_NO_PATHCONV=1` for container paths, absolute `--env-file`, and Windows
   Python writing `/tmp/x` to `C:\tmp\x` while Git Bash reads
   `%TEMP%\x`.

## 6. Not verified here

- **Telegram and Discord delivery** — only the generic webhook path was
  exercised end-to-end. Those senders are code-reviewed, not proven.
- **OCI deployment** — no longer outstanding: Stage 5 ran on 2026-11-13 against
  a real instance (§8). What is still unproven there is *durability*: the host
  has been up for minutes, not days.
- **Long-duration soak** — observation covered ~2 minutes, not days; retention
  pruning (`HISTORY_RETENTION_DAYS`, hourly task) has not fired.
- **Restart policy behaviour** — `restart: unless-stopped` was not tested against
  a host reboot or daemon restart.
- **Load** — five endpoints at 30–300 s intervals; no test of hundreds of
  endpoints or concurrent slow targets.

## 7. Reproduction

```bash
cp .env.example .env                                  # blank unused channels
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f uptime-bot

# drill (see AGENT_GUIDE.md Phase 4-5 for the full block)
docker compose --env-file .env -f docker/docker-compose.yml stop uptime-bot
docker compose --env-file .env -f docker/docker-compose.yml run --rm \
  -e ENDPOINTS_FILE=/app/configs/drill.local.yaml \
  -e FAILURE_THRESHOLD=2 -e ALERT_WEBHOOK_URL=http://host.docker.internal:8912/alert \
  uptime-bot sh -c '...'
```

Teardown: `docker compose --env-file .env -f docker/docker-compose.yml down -v`

---

## 8. Live OCI deployment (Stage 5, 2026-11-13)

Run against a real tenancy in `eu-frankfurt-1`, not a mock. Infrastructure came
from `terraform/` unmodified; only `terraform.tfvars` (gitignored) supplied the
credentials, SSH key and CIDR.

### Provisioning

| Step | Result |
| --- | --- |
| `tofu plan` | `Plan: 6 to add` — VCN, IGW, route table, security list, subnet, instance |
| First `apply` in AD-1 | **`500-InternalError, Out of host capacity`** |
| Retry in AD-2 | `Apply complete! Resources: 1 added` (44 s) |
| Public IP | **129.159.30.105** (private 10.4.1.29) |
| Shape | `VM.Standard.A1.Flex`, 1 OCPU / 1 GB, Ubuntu 24.04, `aarch64` |
| `cloud-init status` | `done`; Docker 29.9.3 present |
| SSH | `ssh ubuntu@129.159.30.105` with the ed25519 key |

The capacity failure is the expected Always Free reality, not a configuration
bug: the same plan succeeded unchanged in the next availability domain, with no
switch to a paid shape.

### Live monitoring from the cloud host

```json
{"ts":"2026-11-13T00:55:17Z","endpoint":"Public API Edge","status_code":200,"latency_ms":96.29,"state":"up","detail":"200 in 96ms"}
{"ts":"2026-11-13T00:55:18Z","endpoint":"Docs (HEAD, redirects allowed as 200)","status_code":200,"latency_ms":76.58,"state":"up"}
{"ts":"2026-11-13T00:58:11Z","endpoint":"Docs (HEAD, redirects allowed as 200)","status_code":200,"latency_ms":61.64,"state":"up"}
{"ts":"2026-11-13T00:58:11Z","endpoint":"Public DNS over TCP","latency_ms":1.09,"state":"up","detail":"ok in 1ms"}
```

| Metric | Live value (Frankfurt) | Local reference (Stage 4) |
| --- | --- | --- |
| `example.com` GET | **97.4 ms avg** | 45.6 ms |
| `example.com` HEAD | **69.1 ms avg** | 34.3 ms |
| `1.1.1.1:53` TCP | **1.1 ms avg** | 7.8 ms |
| History rows | 27 checks / 6 state rows | 22 / 6 |
| Container memory | **19.82 MiB / 128 MiB** | 18.19 MiB |
| Container CPU | 0.01 % | 0.01 % |

Latency is dominated by geography, as predicted in the README cost section: the
cloud host is slower to a US-anycast web endpoint and faster to a European DNS
resolver. No published ports exist on the host — the security list's single
ingress rule is 22/tcp, and Docker confirms nothing is listening.

### Live alert delivery

Same drill as §4, run on the instance, with the receiver in a container on the
compose network:

```json
{"message":"check completed","endpoint":"Drill Target","state":"down","transition":"down","error":"tcp error: Connection refused"}
{"message":"alert dispatched","event":"down","endpoint":"Drill Target","delivered":["webhook"],"configured":["webhook"]}
{"message":"check completed","endpoint":"Drill Target","state":"up","transition":"recovered"}
{"message":"alert dispatched","event":"recovered","endpoint":"Drill Target","delivered":["webhook"],"configured":["webhook"]}
```

`delivered:["webhook"]` means the receiver answered 2xx — verified twice, once
for `down` and once for `recovered`. Final drill state: `up`, 0 failures,
2 successes.

### Two failures worth recording

1. **First drill delivered nothing** (`delivered:[]`) because the webhook pointed
   at `172.17.0.1` (`docker0`) while `compose run` joins its own bridge
   (`172.18.0.1`), and the host firewall drops container-to-host traffic anyway.
   Fixed by putting the receiver in a container on the same compose network and
   addressing it by name (`http://cg-recv:8912/alert`). This is the correct local
   pattern for a host with a 22-only security list.
2. **The bot did not restart after the drill** until an explicit
   `docker compose up -d`; `compose start` alone was not enough in that shell
   sequence. Worth knowing before trusting `restart: unless-stopped` as a
   recovery mechanism.

### Current state

The instance is **left running** for live monitoring: `129.159.30.105`, bot
healthy, alerts armed (channels blank in `.env`, so log-only until you add a
real one). To stop the spend:

```bash
cd terraform && tofu destroy -auto-approve
```
