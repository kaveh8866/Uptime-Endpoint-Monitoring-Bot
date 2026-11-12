# TEST_REPORT.md — local verification (Stage 4)

Recorded on 2026-11-12, Docker 29.8.1 on Windows, Git Bash, project commit
`c9dcbe6`. Reproduce with the commands below; nothing here needs cloud
credentials.

> **Scope of certification:** these results are local, on one developer machine.
> The production target and the only environment certified for hosting is
> **Oracle Cloud Infrastructure (OCI)** with Always Free / trial credits — see
> `MANUAL_PREREQS.md` §8. AWS and GCP are portable by design but untested.

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
- **OCI deployment** — no `tofu apply` has run, so "works on the certified
  target" remains unproven until Stage 5.
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
