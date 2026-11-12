# AGENT_GUIDE.md — Uptime Endpoint Monitoring Bot

Executable playbook for AI coding agents (Cursor, Claude Code, Windsurf) and
humans working like one. Every phase states what to run, what to read back, and
when to **stop instead of improvising**.

> **Rule zero:** never write a token into a tracked file. Channels come from
> `.env` or `-e` at run time; `.env.example` holds placeholders only.
>
> **Cloud posture:** the bot is cloud-agnostic — Docker plus outbound sockets, no
> provider SDK. **Verification and testing certification were performed
> exclusively on Oracle Cloud Infrastructure (OCI)** using Always Free / trial
> credits. AWS and GCP are supported by design (see `terraform/README.md`) but
> were not deployed or certified.

---

## Phase map

| Phase | Scope | Exit criterion |
| --- | --- | --- |
| 0 | Preflight | Docker answers, tree clean, remote correct |
| 1 | Static verification | compose config, `py_compile`, YAML parse all pass |
| 2 | First run + one cycle | one JSON line per endpoint, rows in SQLite |
| 3 | Inspect logs and state | you can explain every state row from the log |
| 4 | Drill: forced outage | exactly one `down`, exactly one `recovered` |
| 5 | Alert delivery | receiver logs the POST; dead channel does not stop probes |
| 6 | OCI provisioning | `tofu plan` shows 22/tcp only, apply succeeds |
| 7 | Verify on the host | service up, cycle logged, drill repeated |

Run from the repository root. Windows: use Git Bash and
`export MSYS_NO_PATHCONV=1` before any `docker`/`docker compose` command that
contains a container path (`/app/configs/…`, `/data/uptime.db`), otherwise Git
Bash rewrites it into a `C:/Program Files/Git/…` path. If compose reports
`couldn't find env file`, pass it absolutely:
`--env-file "$(pwd -W)/.env"`.

---

## Phase 0 — Preflight

```bash
docker version --format '{{.Server.Version}}'
docker compose version
python --version                 # optional, only for static checks
git status --porcelain           # expect empty
git remote -v                    # origin = kaveh8866/Uptime-Endpoint-Monitoring-Bot
```

**Stop if:** the daemon does not answer, the tree is dirty with files you did not
create, or `origin` is someone else's repository.

---

## Phase 1 — Verify the scaffold without starting anything

```bash
docker compose --env-file .env.example -f docker/docker-compose.yml config --quiet
python -m py_compile src/bot/*.py
python -c "import yaml,pathlib;[yaml.safe_load(p.read_text()) for p in pathlib.Path('configs').glob('*.yaml')];print('yaml ok')"
```

Expected: exit 0 for all three. `config --quiet` resolves one service
(`uptime-bot`) with a `/data` volume, `read_only: true` and a `/tmp` tmpfs.

**Stop if:** compose reports unset variables without defaults, or a module fails
to compile. Do not "fix" config by adding defaults to the compose file — the
`.env` contract is deliberate.

---

## Phase 2 — Start the stack and read one cycle

```bash
cp .env.example .env             # channels stay empty: log-only run
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f uptime-bot
```

First lines are the banner, then one JSON object per probe:

```json
{"ts":"2026-11-12T09:00:00Z","level":"info","logger":"bot.main","message":"uptime bot starting","endpoint":"5 endpoint(s)","configured":[],"state":"failure>=3, recovery>=1"}
{"ts":"2026-11-12T09:00:00Z","level":"info","logger":"bot.monitor","message":"check completed","endpoint":"CloudGuard Gateway","status_code":200,"latency_ms":3.4,"error":null,"state":"up","transition":null,"detail":"HTTP 200 in 3.4ms"}
{"ts":"2026-11-12T09:00:01Z","level":"warning","logger":"bot.monitor","message":"check completed","endpoint":"Postgres Port (backup host)","status_code":null,"latency_ms":1.1,"error":"connection refused","state":"unknown","transition":null,"detail":"connection refused"}
```

`state":"unknown"` after one failure is correct: `unknown → down` needs
`FAILURE_THRESHOLD` consecutive failures.

Confirm the state landed on disk (the only writable path in the container):

```bash
docker exec -i cg-uptime python - <<'PY'
import sqlite3
db = sqlite3.connect("/data/uptime.db")
db.row_factory = sqlite3.Row
print([dict(r) for r in db.execute("select * from endpoint_state")])
print("checks:", db.execute("select count(*) from checks").fetchone()[0])
PY
```

**Stop if:** the container restarts in a loop (read `docker logs cg-uptime` — the
error names the YAML file and key), or `endpoint_state` is empty after two
interval periods, or `docker stats --no-stream cg-uptime` approaches the 128 MB
cap.

---

## Phase 3 — Inspect logs and state

The bot writes **one JSON object per line** to stdout, so `docker logs` is
already a queryable stream.

```bash
# transitions only — the events that page a human
docker logs cg-uptime 2>&1 | grep '"transition":"'

# alerts and their delivery result
docker logs cg-uptime 2>&1 | grep -E '"alert dispatched"|"alert suppressed"|"alert channel'

# startup config and prune runs
docker logs cg-uptime 2>&1 | grep -E '"uptime bot starting"|"history pruned"'
```

`jq` makes it pleasant if you have it:

```bash
docker logs cg-uptime 2>&1 | jq -c 'select(.latency_ms > 800) | {endpoint,latency_ms}'
```

State and history (schema is fixed: `checks` is append-only, `endpoint_state` is
the current streak):

```bash
docker exec -i cg-uptime python - <<'PY'
import sqlite3
db = sqlite3.connect("/data/uptime.db"); db.row_factory = sqlite3.Row

print("— current state —")
for r in db.execute("select endpoint,state,failures,successes,changed_at from endpoint_state order by endpoint"):
    print(f"{r['endpoint']:<28} {r['state']:<8} fail={r['failures']} ok={r['successes']}")

print("\n— last hour, per endpoint —")
for r in db.execute("""
    select endpoint, count(*) n,
           round(avg(latency_ms),1) avg_ms, round(max(latency_ms),1) max_ms,
           sum(case when ok=0 then 1 else 0 end) failures
    from checks where checked_at > strftime('%s','now') - 3600
    group by endpoint order by endpoint"""):
    print(f"{r['endpoint']:<28} n={r['n']:<4} avg={r['avg_ms']}ms max={r['max_ms']}ms fail={r['failures']}")

print("\n— most recent failure per endpoint —")
for r in db.execute("""
    select c.endpoint, c.checked_at, c.status_code, c.error
    from checks c join (select endpoint, max(checked_at) t from checks where ok=0 group by endpoint) m
      on c.endpoint=m.endpoint and c.checked_at=m.t
    order by c.endpoint"""):
    print(dict(r))
PY
```

Read it like this: a row in `endpoint_state` with `state='up'` and a non-zero
`failures` means probes are wobbling below the threshold — that is the state
machine working, not a bug.

**Stop if:** `endpoint_state` disagrees with the last log line for that endpoint
(state is written before logging, so they must agree), or timestamps move
backwards (clock/TZ skew between host and container).

---

## Phase 4 — Drill: force an outage, then a recovery

Deterministic and safe: a dedicated one-endpoint config pointing at a port
nothing listens on, then a listener started mid-run. `configs/*.local.yaml` is
gitignored, so the drill never touches the shipped watch list.

```bash
cat > configs/drill.local.yaml <<'YAML'
defaults:
  interval_seconds: 5
  timeout_seconds: 2
endpoints:
  - name: Drill Target
    type: tcp
    host: 127.0.0.1
    port: 8089
YAML
```

Stop the long-running service first — two processes on one SQLite file is the one
thing that produces `database is locked`:

```bash
docker compose -f docker/docker-compose.yml stop uptime-bot
```

Run the bot with a 2-failure threshold and **no channels** (a drill must never
page anyone), start a listener after ~14 s, and let recovery happen:

```bash
docker compose --env-file .env -f docker/docker-compose.yml run --rm \
  -e ENDPOINTS_FILE=/app/configs/drill.local.yaml \
  -e FAILURE_THRESHOLD=2 -e RECOVERY_THRESHOLD=1 \
  -e ALERT_WEBHOOK_URL= -e TELEGRAM_BOT_TOKEN= -e TELEGRAM_CHAT_ID= -e DISCORD_WEBHOOK_URL= \
  uptime-bot sh -c '
    python -m bot.main > /tmp/drill.log 2>&1 &
    bot=$!
    sleep 14
    python -m http.server 8089 --bind 127.0.0.1 > /dev/null 2>&1 &
    web=$!
    sleep 12
    kill -TERM $bot $web 2>/dev/null
    wait $bot 2>/dev/null
    grep -E "check completed|alert" /tmp/drill.log
  '
```

Expected, in order:

- three or more warnings with `"state":"unknown"` / `"down"` and
  `"error":"connection refused"`;
- **exactly one** line with `"transition":"down"`;
- one or more `"state":"up"` lines carrying `"transition":"recovered"` — the
  first success only;
- `"alert suppressed (no channel configured)"` for each transition, proving the
  channel layer is separate from the state machine.

Then inspect what the drill wrote — restart the service and query through it:

```bash
docker compose --env-file .env -f docker/docker-compose.yml start uptime-bot
docker exec cg-uptime python -c "import sqlite3;print(sqlite3.connect('/data/uptime.db').execute(\"select endpoint,state,failures,successes from endpoint_state where endpoint='Drill Target'\").fetchall())"
```

Expected: `('Drill Target', 'up', 0, 1)` — the endpoint ended healthy, having
produced one incident.

**Stop if:** `transition` repeats on every probe (state is not persisted), a
single failure declares DOWN, or no `recovered` appears after the listener
starts. Do not "fix" it by lowering thresholds in `configs/thresholds.yaml`.

Clean up: `rm -f configs/drill.local.yaml` (it is ignored, but leaving it makes
the next drill ambiguous).

---

## Phase 5 — Prove alert delivery

A receiver that actually answers 200 (a plain `http.server` returns 501 to POST,
which the bot correctly reports as a failed channel):

```bash
# terminal A — host-side receiver
python - <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        print("ALERT:", body.decode(), flush=True)
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("0.0.0.0", 8899), H).serve_forever()
PY
```

```bash
# terminal B — same drill, now with a webhook
docker compose -f docker/docker-compose.yml stop uptime-bot
docker compose --env-file .env -f docker/docker-compose.yml run --rm \
  -e ENDPOINTS_FILE=/app/configs/drill.local.yaml \
  -e FAILURE_THRESHOLD=2 -e RECOVERY_THRESHOLD=1 \
  -e ALERT_WEBHOOK_URL=http://host.docker.internal:8899/alert \
  -e TELEGRAM_BOT_TOKEN= -e TELEGRAM_CHAT_ID= -e DISCORD_WEBHOOK_URL= \
  uptime-bot sh -c '
    python -m bot.main > /tmp/drill.log 2>&1 & bot=$!
    sleep 14; python -m http.server 8089 --bind 127.0.0.1 >/dev/null 2>&1 & web=$!
    sleep 12; kill -TERM $bot $web 2>/dev/null; wait $bot 2>/dev/null
    grep -E "alert dispatched|alert channel" /tmp/drill.log
  '
```

- Linux hosts have no automatic `host.docker.internal`: replace it with the
  Docker bridge address (`ip -4 addr show docker0 | grep -oP 'inet \K[\d.]+'`).
- Expected: `"message":"alert dispatched","event":"down","delivered":["webhook"]`
  and a matching POST in terminal A; then the same for `recovered`.

Now prove a broken channel cannot stop monitoring — point it at a dead port and
confirm the loop continues:

```bash
docker compose --env-file .env -f docker/docker-compose.yml run --rm \
  -e ENDPOINTS_FILE=/app/configs/drill.local.yaml -e FAILURE_THRESHOLD=1 \
  -e ALERT_WEBHOOK_URL=http://127.0.0.1:9/dead \
  uptime-bot sh -c 'python -m bot.main > /tmp/dead.log 2>&1 & b=$!; sleep 8; kill -TERM $b; wait $b 2>/dev/null; grep -cE "check completed" /tmp/dead.log; grep -E "alert channel" /tmp/dead.log | head -2'
```

Expected: a non-zero count of completed checks **and** `alert channel
unreachable` warnings — probes never stop because a channel is down.

**Stop if:** a dead channel raises an exception, blocks longer than 5 s per
attempt, or the process exits.

Restore the service: `docker compose --env-file .env -f docker/docker-compose.yml start uptime-bot`.

---

## Phase 6 — OCI provisioning

This is the certified target. AWS/GCP parity is documented, not implemented.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars    # tenancy, compartment, region, public key, CIDR
tofu init && tofu fmt -recursive && tofu validate
tofu test                                      # 4 offline posture assertions, no credentials
tofu plan
tofu apply
tofu output connection_endpoints
```

`tofu test` proves the contract without an account: exactly one ingress rule
(`22/tcp`), egress open to `0.0.0.0/0`, public path wired, A1.Flex at 1 OCPU /
1 GB with a public IP.

**Stop if:** the plan proposes any ingress rule other than 22/tcp, resources
outside the project compartment, or a shape/size outside the Always Free
envelope. Do not accept a paid shape to get past "out of host capacity" — retry
another availability domain first.

---

## Phase 7 — Verify on the host

```bash
ssh ubuntu@<instance_public_ip>
cloud-init status --wait            # "done" before deploying anything
docker version --format '{{.Server.Version}}'

git clone https://github.com/kaveh8866/Uptime-Endpoint-Monitoring-Bot.git
cd Uptime-Endpoint-Monitoring-Bot
cp .env.example .env && chmod 600 .env
$EDITOR .env                        # channels, thresholds, retention
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f uptime-bot
```

Then repeat Phase 3 (state inspection) and Phase 4 (drill) on the host. A
deployment is only certified when the drill produces one `down` and one
`recovered` **there**, not just on the developer machine.

**Stop if:** `cloud-init status` reports errors, Docker is missing (bootstrap did
not finish), or the first cycle cannot reach any public target (egress problem —
check the security list's egress rule before blaming the bot).

---

## What an agent must never do

- Never commit `.env`, `*.db`, `*.tfvars`, `*.tfstate`, `keys/`. Prove hygiene
  with `git check-ignore -v .env` rather than by eye.
- Never put a real token in `.env.example`, a README, a commit message or a test.
- Never send drill alerts to a real channel: blank the four channel variables, as
  Phases 4 and 5 do.
- Never set `FAILURE_THRESHOLD=1` in a shipped config. One timeout would page
  someone for a healthy service.
- Never copy a production `uptime.db` into the repository — endpoint names and
  history are private.
- Never widen the security list to "make it work". The bot has no inbound
  service; if something needs a port, it is a different project.
- Never claim OCI verification before `tofu apply` and a drill on that host have
  both succeeded, and never claim AWS/GCP verification at all.

---

## Troubleshooting quick table

| Symptom | Cause | Action |
| --- | --- | --- |
| Restart loop | YAML/validation error at startup | `docker logs cg-uptime`, fix the named key |
| `configuration rejected` | duplicate name, interval < 5, missing field | the error states the rule |
| Everything DOWN instantly | bot probes from inside the container | use `host.docker.internal` / host IP |
| `database is locked` | two processes on one DB | stop the service before drilling |
| No `down` in drill | threshold not reached yet | wait `interval × FAILURE_THRESHOLD` |
| `delivered":[]` | receiver returned non-2xx | use the Phase 5 receiver, not `http.server` |
| `tofu apply` capacity error | A1 free capacity | retry another AD |
| `cloud-init status` errors | bootstrap failed | `sudo journalctl -u cloud-init` on the host |

---

## Certification and portability

| Layer | Portable? | Certified? |
| --- | --- | --- |
| Bot runtime (`src/`, `docker/`, `configs/`) | Yes — Docker + outbound TCP/HTTP only | ✅ on OCI host |
| IaC (`terraform/`) | Yes — provider specifics inside `modules/*` | ✅ OCI; AWS/GCP documented only |
| Alert channels | Yes — provider HTTP APIs | ✅ webhook path proven locally; Telegram/Discord need live tokens |

The certification statement is intentionally narrow: **OCI, Always Free / trial
credits, one region, one A1.Flex host**. It covers the container stack starting,
probes executing, transitions firing exactly once, and alerts delivering. It does
not cover your targets, third-party channel availability, or free-tier capacity
at apply time.
