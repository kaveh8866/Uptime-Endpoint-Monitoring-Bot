# SECURITY_AUDIT.md — zero-leak scan and sign-off

Audited 2026-11-12 against commit `af5898b` (Stage 7), repository
`kaveh8866/Uptime-Endpoint-Monitoring-Bot`.

**Result: PASS — no private keys, bot tokens, webhook secrets or live `.env`
files exist in the working tree or in any commit.** Two files were flagged by the
file-name scan and both are legitimate; details below.

---

## 1. Secret-shaped files on disk

```bash
find . -path ./.git -prune -o -path ./terraform/.terraform -prune -o -type f \
  \( -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' \
     -o -name '.env' -o -name '.env.*' -o -name '*.tfstate' -o -name '*.tfvars' \) -print
```

| Finding | Verdict |
| --- | --- |
| `./.env.example` | **Expected.** Tracked template; every value is a placeholder (`your_telegram_bot_token_here`, `https://example.invalid/…`) |
| `./terraform/terraform.tfstate` | **Expected, gitignored.** Empty state written by `tofu destroy`: 2.8 KB, version 4, **0 resources, 0 outputs, 0 OCID references** |

Nothing else matched: no `.pem`, no `.key`, no `.p12`/`.pfx`, no `.env`, no
`terraform.tfvars`.

## 2. Content scan — tracked files

```bash
git grep -nIE "(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|\
xox[abps]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\
[0-9]{6,}:[A-Za-z0-9_-]{30,})" -- . ':!.env.example'
```

**No matches** across 37 tracked files. Patterns cover OpenAI-style keys, AWS
access key IDs, GitHub tokens, Slack tokens, Google API keys, PEM private key
headers, and Telegram bot-token shape.

## 3. History scan — every commit

```bash
git log -p --all | grep -nE "(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|\
gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
```

**No matches** in the full history (`97c2ce7` → `af5898b`). Because no secret was
ever committed, no history rewrite or credential rotation is required.

> If a secret *does* reach a commit: rotate it at the provider first, then
> rewrite history. Deleting the line afterwards is not enough — the value stays
> in the object database until garbage collection, and clones keep it.

## 4. `.gitignore` effectiveness

Probes were created with `git check-ignore -q` (no files written):

| Path that must be ignored | Result |
| --- | --- |
| `.env`, `.env.production` | ignored |
| `secrets.pem`, `token.key` | ignored |
| `*.p12`, `*.pfx`, `*.crt` | ignored |
| `uptime.db`, `data/backup.sqlite` | ignored |
| `terraform/terraform.tfstate`, `terraform/terraform.tfvars` | ignored |
| `terraform/.terraform/` (provider binaries) | ignored |
| `configs/local/prod.yaml`, `configs/*.local.yaml` | ignored |
| `logs/`, `*.log` | ignored |

| Path that must stay trackable | Result |
| --- | --- |
| `.env.example` | trackable |
| `configs/endpoints.yaml`, `configs/thresholds.yaml` | trackable |
| `terraform/terraform.tfvars.example` | trackable |

## 5. `.env.example` structure

12 `KEY=value` entries, all non-empty placeholders, no real credential:

| Group | Keys |
| --- | --- |
| Paths | `ENDPOINTS_FILE`, `THRESHOLDS_FILE`, `STATE_DB_PATH` |
| Behaviour | `HISTORY_RETENTION_DAYS`, `FAILURE_THRESHOLD`, `RECOVERY_THRESHOLD`, `LOG_LEVEL` |
| Channels | `ALERT_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL` |
| Runtime | `TZ` |

Known sharp edge, documented in the file and in `MANUAL_PREREQS.md` §3d: because
the placeholders are non-empty, copying `.env.example` untouched makes all three
channels look configured. Blank the channels you do not use.

## 6. Container and runtime posture (verified in Stage 4)

| Control | Evidence |
| --- | --- |
| Non-root process | `uid=999(bot)` |
| Immutable rootfs | `touch /app/x` → `Read-only file system` |
| No inbound port | compose publishes nothing; only `22/tcp` is open on the host |
| Resource caps | 128 MB / 0.2 CPU / 128 pids; observed 18.19 MiB, 0.01 % CPU |
| Log rotation | `json-file`, 10 MB × 2 |
| Privilege escalation | `security_opt: no-new-privileges:true` |
| Credential reach | the bot holds no cloud credential; alerts are outbound HTTPS |

## 7. Residual risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Discord webhook URL is a bearer credential in `.env` | Medium | Keep `.env` at `0600`; rotate the webhook in Discord if it leaks |
| Telegram bot token allows full bot control | Medium | Same; revoke via BotFather on exposure |
| No automated secret scanning in CI | Low | Run the §2/§3 greps before each release (a repo has no CI yet) |
| `configs/endpoints.yaml` may reveal internal hostnames if edited in place | Low | Use `configs/local/*.yaml` (gitignored) for private targets |
| Host SSH exposure | Medium | `ssh_allowed_cidr` must be your address, not `0.0.0.0/0`; no other ingress exists |
| Alert payloads contain endpoint names | Low | Names end up in the channel provider's history — name targets accordingly |

## 8. Sign-off

- Working tree: **clean**, 37 tracked files, 0 untracked.
- Secrets in tree: **none**. Secrets in history: **none**.
- Ignore rules: **active and effective**; templates remain trackable.
- Container hardening: **verified by execution**, not by reading the compose file.
- Scope limit: this audit covers the repository and the local container runtime.
  It does **not** cover the OCI tenancy (no credentials were available) or a
  deployed host, because no deployment has occurred.
