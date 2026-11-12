# Infrastructure — OCI (OpenTofu / Terraform)

Provisions the host that runs the Stage 1 container stack: a network, a public
subnet locked to SSH, and one Always Free eligible Ampere host. No load balancer,
no bucket, no managed database — the bot needs outbound connectivity and SQLite
on the boot volume, and nothing else.

```text
terraform/
├── versions.tf              provider pin, auth, backend note
├── variables.tf             validated input contract
├── main.tf                  module wiring + cloud-init rendering
├── outputs.tf               public IP and connection strings
├── cloud-init.yaml.tftpl    first boot: Docker, Compose plugin, app directory
├── terraform.tfvars.example operator template
└── modules/
    ├── network/             VCN, IGW, route table, security list, subnet
    └── compute/             A1.Flex instance, VNIC address lookups
```

## What gets created

| Resource | Configuration | Why |
| --- | --- | --- |
| VCN | `10.4.0.0/16`, own route table | Isolated network, no shared defaults |
| Internet gateway | enabled | Outbound probes and alert delivery |
| Public subnet | `10.4.1.0/24`, public IP on VNIC | Only outbound traffic and SSH matter |
| Security list | **ingress TCP 22 only**, egress all | The bot listens on nothing |
| Compute instance | `VM.Standard.A1.Flex`, 1 OCPU / 1 GB, 50 GB boot | Always Free ARM; container capped at 0.2 CPU |

The 22-only ingress rule is not a placeholder: the bot has no server, no
dashboard and no metrics port. Its whole job is outbound. If you later add a
status page, open exactly that port and nothing else.

Pin `ssh_allowed_cidr` to your own address (`curl -s https://ifconfig.me`) before
using this anywhere real.

## Usage

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # gitignored
$EDITOR terraform.tfvars                       # tenancy, compartment, key, CIDR

tofu init
tofu fmt -recursive
tofu validate
tofu plan
tofu apply
```

Outputs:

```text
instance_public_ip   = "203.0.113.42"
connection_endpoints = { ssh = "ssh ubuntu@203.0.113.42", ... }
deploy_next_step     = "1. ssh ubuntu@… 2. git clone …"
```

Then follow `deploy_next_step`: clone the repo on the host, copy `.env.example`,
fill in the alert channels and target endpoints, and bring the stack up. Nothing
in this directory knows your webhook tokens — the deployment credentials never
enter Terraform state.

## Authentication

Two supported paths, both driven by variables:

- `config_file_profile = "DEFAULT"` — read `~/.oci/config` (recommended).
- Inline `user_ocid` + `fingerprint` + `private_key_path` — key stays outside the
  repository.

`terraform.tfvars`, `*.tfstate` and `*.pem` are gitignored. State is local for
Stage 2; move it to an encrypted remote backend before sharing the environment.

## Portability (AWS / GCP)

Every resource has a 1:1 equivalent; the mapping tables live at the top of
`modules/network/main.tf` and `modules/compute/main.tf`. The module interfaces
were kept provider-neutral for exactly this reason:

| Concept | OCI (implemented) | AWS | GCP |
| --- | --- | --- | --- |
| Network | `oci_core_vcn` | `aws_vpc` | `google_compute_network` |
| Gateway | `oci_core_internet_gateway` | `aws_internet_gateway` | implicit on a default-route VPC |
| Routes | `oci_core_route_table` | `aws_route_table` + `aws_route` | `google_compute_route` |
| Firewall | `oci_core_security_list` | `aws_security_group` + rules | `google_compute_firewall` |
| Subnet | `oci_core_subnet` | `aws_subnet` | `google_compute_subnetwork` |
| Instance | `oci_core_instance` + `shape_config` | `aws_instance` (`t4g.small`) | `google_compute_instance` (`e2-micro`) |
| Bootstrap | `metadata.user_data` | `user_data` | `metadata_startup_script` |
| Public IP | `oci_core_vnic.public_ip_address` | `aws_eip` / instance attr | `network_interface.access_config.nat_ip` |

Variables named `vcn_cidr`, `subnet_cidr`, `ssh_allowed_cidr`, `instance_shape`,
`instance_ocpus`/`instance_memory_gbs` and `ssh_public_key` either already match
their AWS/GCP counterparts or map onto one field. Porting means writing sibling
`modules/network-aws` and `modules/compute-aws` directories with the same
variable names and outputs, then swapping the two `source` lines in `main.tf` —
`docker/`, `src/` and `configs/` stay untouched, because the container is
provider-agnostic.

On AWS the free-tier equivalent of 1 OCPU / 1 GB ARM is `t4g.small` (750 h/month
for 12 months) or `t3.micro`; on GCP it is `e2-micro` in a free-tier region. The
security group rule set is identical: SSH from one CIDR, all egress.

## Verifying without an account

`tofu validate` checks syntax, types, variable validations and module wiring
without contacting OCI. `tofu plan` needs real credentials, since it resolves
availability domains and the Ubuntu image for the chosen shape.

```bash
tofu init -backend=false && tofu validate
```
