# terraform/ — OCI provisioning (Stage 2)

Reserved for the OpenTofu configuration that hosts this bot on Oracle Cloud
Infrastructure. Nothing is provisioned yet; Stage 1 ships the service and the
container stack only.

## Planned scope

| Module | Resources |
| ------ | --------- |
| `network` | VCN, internet gateway, route table, security list (**22/tcp only** — the bot makes outbound connections, it never needs ingress beyond SSH), public subnet |
| `compute` | `VM.Standard.A1.Flex` (1 OCPU / 1 GB), Ubuntu 24.04 aarch64, cloud-init installs Docker and clones this repository |
| `monitoring` (optional) | Nothing billable: the bot reports over outbound HTTPS to the configured webhook, so no load balancer or public endpoint is required |

## Why a bot needs so little infrastructure

The design is outbound-only by construction:

- no inbound port for the dashboard, because there is no dashboard in Stage 1;
- state lives in SQLite on the boot volume, so no managed database;
- alerts leave via HTTPS to Slack/Discord/Telegram/webhook, so no mail relay.

That keeps the whole deployment inside the Always Free envelope — see the cost
table in the root README once Stage 2 lands.

## Portability

The same pattern maps to AWS (`aws_vpc` + `aws_instance` on `t3.micro`, security
group with 22/tcp) and GCP (`google_compute_network` + `google_compute_instance`
on `e2-micro`, firewall rule for 22/tcp). The bot itself is provider-blind: it
only needs outbound HTTPS and a writable directory for `/data`.
