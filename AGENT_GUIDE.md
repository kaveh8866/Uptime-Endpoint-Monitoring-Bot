# AGENT_GUIDE.md — Uptime Endpoint Monitoring Bot

An executable playbook for AI coding agents (Cursor, Claude Code, Windsurf) and
humans acting like one. Each phase lists what to run, what to read back, and when
to **stop** instead of improvising.

> **Rule zero:** never write a token into a tracked file. Channels are configured
> through `.env` or the environment, and `.env.example` carries placeholders only.

---

## Phase map

| Phase | Scope | State |
| ----- | ----- | ----- |
| 0 | Preflight: Docker reachable, clean tree, Python present | ✅ |
| 1 | Verify the scaffold without starting anything | ✅ |
| 2 | Start the stack and read one full cycle | ✅ |
| 3 | Prove the state machine: forced outage → DOWN → RECOVERED | ⬜ |
| 4 | Verify a real notification channel | ⬜ |
| 5 | OCI provisioning with OpenTofu (`terraform/`) | ✅ written + validated, apply pending credentials |

---

## Phase 0 — Preflight

```bash
docker version --format '{{.Server.Version}}'
python --version            # 3.11+ for the no-Docker path
git status --porcelain      # must be empty
git remote -v               # origin = kaveh8866/Uptime-Endpoint-Monitoring-Bot
```

**Stop if:** the engine does not answer, the tree is dirty in files you did not
create, or `origin` points elsewhere.

---

## Phase 1 — Verify the scaffold

```bash
docker compose --env-file .env.example -f docker/docker-compose.yml config --quiet
python -m py_compile src/bot/*.py
python -c "import yaml, pathlib; [yaml.safe_load(p.read_text()) for p in pathlib.Path('configs').glob('*.yaml')]; print('yaml ok')"
```

Expected: exit 0 for all three; the compose file resolves to one service
(`uptime-bot`) with a `/data` volume and a read-only rootfs.

**Stop if:** compose warns about unset variables without defaults, or a probe
module fails to compile.

---

## Phase 2 — Start the stack and read a cycle

```bash
cp .env.example .env        # channels stay empty: log-only run
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f uptime-bot
```

Expected on the first cycle, one JSON line per endpoint:

```json
{"level":"info","message":"check completed","endpoint":"CloudGuard Gateway","status_code":200,"latency_ms":3.4,"state":"up","transition":null}
```

Then confirm the state really landed in SQLite:

```bash
docker exec cg-uptime python - <<'PY'
import sqlite3
db = sqlite3.connect("/data/uptime.db")
print(db.execute("select endpoint, state, failures, successes from endpoint_state").fetchall())
print(db.execute("select count(*) from checks").fetchone())
PY
```

**Stop if:** the container restarts in a loop (read `logs` — most often a YAML
error in `configs/endpoints.yaml`), or `endpoint_state` is empty after two
intervals.

---

## Phase 3 — Prove the state machine

Point one endpoint at a closed port, then watch the streak logic do its job:

```bash
# a port nothing listens on → connection refused
docker exec cg-uptime sh -c 'python -c "import socket;s=socket.socket();s.settimeout(1);print(s.connect_ex((\"127.0.0.1\",9)))"'

# with FAILURE_THRESHOLD=2, the second consecutive failure must emit transition=down
docker compose --env-file .env -f docker/docker-compose.yml \
  run --rm -e FAILURE_THRESHOLD=2 uptime-bot python -m bot.main
```

Then reverse it: start a listener, let a probe succeed, and confirm
`transition="recovered"` appears exactly once — not on every subsequent success.

**Stop if:** a transition repeats on every probe (the state row is not being
persisted) or a single failure declares DOWN (the streak is not counted).

---

## Phase 4 — Verify a notification channel

Use a throwaway receiver rather than a real workspace:

```bash
# terminal A — local receiver on the host
python -m http.server 8899

# terminal B — point the bot at it
docker compose --env-file .env -f docker/docker-compose.yml \
  run --rm -e ALERT_WEBHOOK_URL=http://host.docker.internal:8899/alert uptime-bot python -m bot.main
```

Expected: `alert dispatched` with `delivered=["webhook"]`; the receiver logs a
POST. Then set the URL to `http://127.0.0.1:9/dead` and confirm the bot logs a
warning and **keeps probing** — alerting must never stop monitoring.

**Stop if:** a dead channel raises, blocks longer than 5 s, or the loop exits.

---

## Phase 5 — OCI provisioning (Stage 2)

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # tenancy, compartment, region, SSH key
tofu init && tofu fmt -recursive && tofu validate
tofu test                                     # offline posture check, no credentials
tofu plan && tofu apply
tofu output connection_endpoints
```

The security list must open **22/tcp only**: the bot makes outbound requests, so
it needs no ingress port and no load balancer.

**Stop if:** the plan wants to publish any port other than 22, or creates
resources outside the project compartment.

---

## Hard limits

- Never commit `.env`, `*.db`, `*.tfvars`, `*.tfstate`, `keys/` — the
  `.gitignore` blocks them, and `git check-ignore` is the way to prove it.
- Never set `FAILURE_THRESHOLD=1` in a shipped config: a single timeout would
  page someone for a healthy service.
- Never restore or copy a production `uptime.db` into this repository: it carries
  your private endpoint names and history.
- Never describe the bot as "verified on OCI" before `tofu apply` has actually
  run: `tofu validate` and `tofu test` prove the configuration, not the host.
