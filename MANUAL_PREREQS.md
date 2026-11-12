# MANUAL_PREREQS.md — operator checklist

Everything a human must prepare **before** running a deploy script or handing the
repository to an agent. Work top to bottom; each section ends with a verification
you can actually run.

Budget: ~10 minutes for a log-only local run, ~30 minutes including OCI
provisioning.

> **The bot itself is cloud-agnostic.** It is a Docker workload that makes
> outbound HTTP/TCP connections and posts JSON. It has no provider SDK, no cloud
> metadata dependency and no managed-service requirement.
>
> **Verification and testing certification were performed exclusively on Oracle
> Cloud Infrastructure (OCI)**, on an Always Free / trial-credit tenancy. AWS and
> GCP are supported by design and documented in `terraform/README.md`, but they
> have **not** been deployed or certified.

---

## 0. What you need

| Item | Required? | Notes |
| --- | --- | --- |
| Docker Engine 24+ with Compose v2 | ✅ local run | `docker compose version` |
| Git | ✅ | to clone and to prove `.env` is ignored |
| Python 3.11+ | optional | only for the no-Docker path and YAML checks |
| Public endpoints to monitor | ✅ | or localhost targets, see §4 |
| One alert channel | optional | log-only works; you get no page |
| OCI tenancy + API key | for the hosted run | §6 |
| SSH key pair | for the hosted run | §6 |

---

## 1. Local preflight

```bash
docker version --format '{{.Server.Version}}'   # expect 24.x or newer
docker compose version                          # expect v2.x
git --version
python --version                                # optional, 3.11+
```

- [ ] Docker answers (not "Cannot connect to the Docker daemon").
- [ ] Compose is the v2 plugin (`docker compose`), not the old `docker-compose`.

> **Windows note:** run the commands from Git Bash. `export MSYS_NO_PATHCONV=1`
> stops Git Bash from rewriting container paths such as `/data/uptime.db` into
> `C:/Program Files/Git/data/uptime.db`. Do this before any `docker exec`,
> `docker cp` or `docker compose run -e SOME_PATH=/app/...`. If compose reports
> `couldn't find env file`, pass it absolutely: `--env-file "$(pwd -W)/.env"`.

---

## 2. Clone and confirm you are on a clean tree

```bash
git clone https://github.com/kaveh8866/Uptime-Endpoint-Monitoring-Bot.git
cd Uptime-Endpoint-Monitoring-Bot
git status --porcelain      # expect empty output
git remote -v               # origin must be kaveh8866/Uptime-Endpoint-Monitoring-Bot
```

- [ ] Tree is clean.
- [ ] `origin` points at your fork/repository, not someone else's.

---

## 3. Obtain notification credentials (pick any)

All channels are optional and independent. Leave a channel empty in `.env` and
the bot logs `alert suppressed (no channel configured)` instead of sending.

### 3a. Telegram (recommended: mobile push, no server)

1. In Telegram, message **@BotFather** → `/newbot` → choose a name and a username
   ending in `bot`.
2. BotFather returns a token shaped like `<digits>:<random-string>`. That is
   `TELEGRAM_BOT_TOKEN`.
3. Open your new bot and send it any message (`/start`). A bot cannot message you
   first.
4. Fetch the chat id:

   ```bash
   curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates" \
     | python -c "import json,sys;d=json.load(sys.stdin);print(d['result'][-1]['message']['chat']['id'])"
   ```

   Private chats return a positive id; groups return a negative id (`-100…`).
   Put it in `TELEGRAM_CHAT_ID`.
5. Verify delivery before wiring the bot:

   ```bash
   curl -s -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/sendMessage" \
     -d "chat_id=<TELEGRAM_CHAT_ID>" -d "text=CloudGuard uptime channel test"
   ```

- [ ] The test message arrived on your phone.
- [ ] Both values are recorded somewhere outside the repository.

> If `getUpdates` returns an empty `result`, message the bot again and re-run it.
> If a webhook is set on the bot, `getUpdates` is blocked; delete it with
> `curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`.

### 3b. Discord (team visibility)

1. Server settings → **Integrations** → **Webhooks** → **New Webhook**.
2. Pick the channel, copy the **Webhook URL**.
3. That URL is `DISCORD_WEBHOOK_URL`. It is a credential: anyone holding it can
   post to that channel.

- [ ] Test: `curl -s -X POST "<DISCORD_WEBHOOK_URL>" -H "Content-Type: application/json" -d '{"content":"CloudGuard uptime channel test"}'` → message appears in the channel.

### 3c. Generic HTTP webhook (Slack, Teams, n8n, Grafana, your own receiver)

1. Create an incoming webhook in the target system.
2. Use its URL as `ALERT_WEBHOOK_URL`.
3. The bot POSTs `application/json`:

   ```json
   {"text":"[CloudGuard][DOWN] Public API Edge: status 503","event":"down",
    "endpoint":"Public API Edge","detail":"status 503","source":"uptime-bot"}
   ```

   `text` is what Slack/Teams render; the other fields are for automation.

- [ ] Your receiver accepts a test POST of the body above.

### 3d. Log-only (no credentials)

Leave all four channel variables empty. Alerts are written to the log as
`alert suppressed (no channel configured)`. Valid for development and for the
state-machine drill.

---

## 4. Configure what gets monitored — `configs/endpoints.yaml`

```yaml
defaults:
  interval_seconds: 60      # per-endpoint probe cadence
  timeout_seconds: 5        # a slow answer is a failed answer
  expected_status: [200]    # any other code counts as unhealthy

endpoints:
  - name: CloudGuard Gateway        # unique; this is the alert's identity
    type: http
    url: http://127.0.0.1:8080/health
    interval_seconds: 30
    expected_status: [200]

  - name: Postgres Port
    type: tcp
    host: 127.0.0.1
    port: 5432
    timeout_seconds: 3
```

Rules that matter:

- [ ] **Unique names.** Duplicate names are rejected at startup — state is keyed
      by name.
- [ ] **`interval_seconds >= 5`.** Anything lower is rejected; a 1-second poller
      is a load test, not a monitor.
- [ ] **`type: http`** needs `url`; optional `method` (default `GET`),
      `headers`, `expected_status` (default `[200]`).
- [ ] **`type: tcp`** needs `host` + `port`; success means the TCP handshake
      completes, nothing is sent.
- [ ] **Reachability.** The bot probes *from where it runs*. `127.0.0.1` means
      "inside the container", so a host-local service needs the host's address
      or `host.docker.internal` (Docker Desktop) — not `localhost`.
- [ ] **Expected codes.** Redirects are followed, so the recorded status is the
      final one. If an API returns `204` or `401` by design, list it in
      `expected_status` or every probe will be a failure.
- [ ] **No private hostnames in the committed file.** Keep real internal targets
      in `configs/local/*.yaml` or `configs/*.local.yaml` (both gitignored) and
      point `ENDPOINTS_FILE` at that copy.

Verify before starting:

```bash
python -c "import yaml;d=yaml.safe_load(open('configs/endpoints.yaml'));print(len(d['endpoints']),'endpoints ok')"
```

---

## 5. Fill in `.env` securely

```bash
cp .env.example .env
chmod 600 .env                 # owner-only; skip on Windows, use ACLs instead
$EDITOR .env                   # never paste secrets into a tracked file
```

| Variable | Meaning | Safe default |
| --- | --- | --- |
| `ENDPOINTS_FILE` | watch list path inside the container | `/app/configs/endpoints.yaml` |
| `THRESHOLDS_FILE` | thresholds path | `/app/configs/thresholds.yaml` |
| `STATE_DB_PATH` | SQLite file | `/data/uptime.db` |
| `HISTORY_RETENTION_DAYS` | history pruned beyond this | `14` |
| `FAILURE_THRESHOLD` | consecutive failures → DOWN | `3` |
| `RECOVERY_THRESHOLD` | consecutive successes → RECOVERED | `1` |
| `LOG_LEVEL` | `INFO` or `DEBUG` | `INFO` |
| `ALERT_WEBHOOK_URL` | generic JSON endpoint | empty = off |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram pair; both required | empty = off |
| `DISCORD_WEBHOOK_URL` | Discord webhook | empty = off |
| `TZ` | log timestamp zone | `UTC` |

Then prove the boundary actually holds:

```bash
git check-ignore -v .env        # must print a matching rule from .gitignore
git status --porcelain          # must NOT list .env
```

- [ ] `.env` is ignored and untracked.
- [ ] No token appears in `configs/`, `README.md` or any tracked file:

  ```bash
  git grep -nE "(TELEGRAM_BOT_TOKEN|DISCORD_WEBHOOK_URL)=.+" -- . ':!.env.example' \
    || echo "no live secrets tracked"
  ```

- [ ] If a token ever reaches a commit: rotate it at the provider **first**, then
      rewrite history. Deleting the line is not enough — the value stays in the
      object database.

---

## 6. Cloud prerequisites (OCI) — only for the hosted run

The bot runs anywhere Docker runs; OCI is the certified target. You need:

1. **Tenancy OCID** — OCI console → profile → Tenancy.
2. **Compartment OCID** — Identity → Compartments (the root compartment works,
   a dedicated one is cleaner).
3. **API signing key** — profile → *My profile* → *API keys* → *Add API key* →
   generate a key pair. Keep the private key outside the repository and register
   its fingerprint in `~/.oci/config`:

   ```ini
   [DEFAULT]
   user=<user-ocid>
   fingerprint=<aa:bb:…>
   tenancy=<tenancy-ocid>
   region=eu-frankfurt-1
   key_file=~/.oci/oci_api_key.pem
   ```

4. **SSH key pair** — `ssh-keygen -t ed25519 -C "cloudguard-uptime"`. Terraform
   wants the **public** key content (`.pub`), never the private one.
5. **Your egress IP** for `ssh_allowed_cidr` — `curl -s https://ifconfig.me`.
   The template defaults to `0.0.0.0/0`; narrow it.
6. **Always Free capacity** — A1.Flex is `1 OCPU / 1 GB`. If apply fails with
   "out of host capacity", retry another availability domain before changing
   shape; do not silently switch to a paid shape.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars        # tenancy, compartment, profile, public key, CIDR
tofu init && tofu fmt -recursive && tofu validate
tofu plan                       # review: 6 resources, 22/tcp as the only ingress, A1.Flex
tofu apply
tofu output connection_endpoints
ssh ubuntu@<instance_public_ip>
cloud-init status --wait        # must report "done" before deploying
docker version --format '{{.Server.Version}}'
```

- [ ] `tofu plan` shows **no ingress rule other than 22/tcp**.
- [ ] `cloud-init status --wait` returns `done` and `docker version` answers.
- [ ] `ssh_allowed_cidr` is your address, not `0.0.0.0/0`.
- [ ] `terraform.tfvars` is gitignored.

---

## 7. First hosted run and smoke test

```bash
git clone https://github.com/kaveh8866/Uptime-Endpoint-Monitoring-Bot.git
cd Uptime-Endpoint-Monitoring-Bot
cp .env.example .env && chmod 600 .env
$EDITOR .env                              # channels + thresholds
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml logs -f uptime-bot
```

- [ ] First cycle logs one JSON line per endpoint.
- [ ] `docker compose ... ps` shows `cg-uptime` as `running` (not restarting).
- [ ] Memory stays well under the 128 MB cap: `docker stats --no-stream cg-uptime`.
- [ ] A real alert arrives (see `AGENT_GUIDE.md` Phase 4) — or you have
      consciously accepted log-only operation.

---

## 8. Certification and portability

- **Design:** portable. The bot is provider-agnostic (Docker + outbound
  sockets); the IaC isolates provider specifics inside `terraform/modules/*`
  with a documented AWS/GCP mapping table.
- **Certified:** **OCI only**, Always Free / trial credits. The networking
  contract (VCN + public subnet + security list open on 22/tcp only, all egress)
  and the compute contract (A1.Flex, cloud-init bootstrap, Docker stack) are what
  were validated there.
- **Not certified:** AWS EC2 and GCP Compute Engine. The configuration is
  portable and the mapping is documented, but no deployment has been performed
  on either, so do not describe this project as "AWS/GCP tested".
- **What "certified" covers:** the container stack starting, probes running,
  transitions firing and alerts delivering on the OCI host. It does not cover
  your own targets, your channel provider's uptime, or capacity availability in
  a region at the moment you apply.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Container restarts every few seconds | YAML/validation error | `docker logs cg-uptime` → the error names the file and key |
| `configuration rejected` on start | duplicate names, interval < 5, missing `url`/`host` | fix `configs/endpoints.yaml`; the message states the rule |
| All endpoints DOWN immediately | bot probes from inside the container | use `host.docker.internal` or the host IP, not `localhost` |
| `alert channel unreachable` | wrong URL, blocked egress, DNS | `docker exec cg-uptime python -c "import socket;print(socket.gethostbyname('api.telegram.org'))"` |
| Alerts sent but never arrive | Telegram chat id wrong / bot never messaged | re-run §3a step 4 |
| `database is locked` | two bot processes on one DB file | run one instance; stop the service before a drill |
| No data in SQLite | wrong `STATE_DB_PATH` or volume not mounted | `docker inspect cg-uptime --format '{{json .Mounts}}'` |
| `tofu apply` → out of host capacity | A1 free capacity exhausted | retry another AD; keep the shape free-tier |

---

## 10. Security checklist (before you call it deployed)

- [ ] `.env` is `0600`, untracked, and absent from `git status`.
- [ ] No token in any tracked file (`git grep` in §5).
- [ ] `ssh_allowed_cidr` is a single address, not the internet.
- [ ] Security list has exactly one ingress rule: `22/tcp`.
- [ ] Discord webhook / Telegram token can be rotated without redeploying code —
      they live only in `.env`.
- [ ] You can restore service: `docker compose --env-file .env -f docker/docker-compose.yml up -d --force-recreate`.
- [ ] Backup of `uptime.db` is *not* in the repository (it contains your private
      endpoint names and history).
