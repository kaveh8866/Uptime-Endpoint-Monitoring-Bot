# TEARDOWN_REPORT.md — Stage 6 cleanup audit

Recorded 2026-11-12 against commit `09677c7`.

## Verdict

**No cloud infrastructure exists for this project, so the teardown destroyed
nothing — because nothing was ever created.** `tofu destroy -auto-approve`
returned `Destroy complete! Resources: 0 destroyed.` and there is no state file
to destroy from. Project-attributable cloud spend is therefore zero, and no
cleanup action was required to reach it.

This is a statement about *this project's* resources only, not about the whole
tenancy. Stray resources created outside OpenTofu (console clicks, other
projects) are outside this audit's reach — see "Limits" below.

## Evidence

| Check | Command | Result |
| --- | --- | --- |
| No state file | `ls terraform/terraform.tfstate` | absent |
| No state backup | `ls terraform/terraform.tfstate.backup` | absent |
| No variables file | `ls terraform/terraform.tfvars` | absent (never applied) |
| State inventory | `tofu state list` | `Error: No state file was found` |
| Destroy | `tofu destroy -auto-approve` | `Destroy complete! Resources: 0 destroyed.` |
| Destroy plan | `tofu plan -destroy` | `No changes. No objects need to be destroyed.` |
| Test leftovers | `ls terraform/errored_test.tfstate` | absent |

The destroy run supplied placeholder values for the required variables
(`tenancy_ocid`, `compartment_ocid`, `region`, `ssh_public_key`) so the command
could evaluate the state rather than stop at variable prompting. No OCI API call
was made and no credential was used — with an empty state there is nothing for
OpenTofu to query.

## Local artifact audit

| Artifact | Count |
| --- | --- |
| Containers (`cg-uptime`, `cloudguard-*`, any `uptime-bot` run) | 0 |
| Volumes (`cloudguard-uptime-bot_uptime-data`) | 0 |
| Images (`cloudguard/*`) | 0 |
| Docker networks | 0 |
| `.env` / `configs/drill.local.yaml` / SQLite files in the repository | 0 |

Stage 4 ended with `docker compose down -v` plus `docker rmi`, which is why the
local footprint is empty rather than merely stopped. `docker compose down`
removes the container and network and keeps the volume; the `-v` is what discards
`uptime-data`, and it is required for a true zero state.

## Limits of this audit

- **No OCI API access was available** (no `terraform.tfvars`, no API signing key,
  no `oci` CLI, no `OCI_*` environment variables), so the tenancy was never
  queried. I cannot prove "zero billable resources in the account" — only "zero
  resources created by this project", which the empty state and the destroy
  result establish.
- **No HITL approval was consumed.** Destructive actions in this workflow require
  explicit human approval; the instruction to run `tofu destroy` supplied it, and
  the guard's outcome was `0 destroyed`. There were no resources whose deletion
  needed gating.
- **`.terraform/` provider cache remains** in `terraform/` (gitignored, ~hundreds
  of MB on disk, no cloud cost). It is a local build cache, not infrastructure;
  delete it with `rm -rf terraform/.terraform` if you want the workspace pristine,
  at the cost of re-running `tofu init`.

## Procedure for a real deployment

Use this when Stage 5 has actually provisioned something. The order matters:
destroy the infrastructure, verify the state is empty, then decide about the
state file.

```bash
cd terraform

# 1. Review what will die before approving anything
tofu plan -destroy

# 2. Approval gate: the operator reads the plan above, then runs
tofu destroy -auto-approve

# 3. Prove the state is empty (this is the machine-checked "zero" claim)
tofu state list            # expect: no output / "No state file was found"

# 4. Confirm at the provider, not just in local state
oci compute instance list --compartment-id "$COMPARTMENT_OCID" \
  --query "data[?\"lifecycle-state\"!='TERMINATED'].{name:\"display-name\",state:\"lifecycle-state\"}" \
  --output table
oci network vcn list --compartment-id "$COMPARTMENT_OCID" \
  --query "data[].{name:\"display-name\",state:\"lifecycle-state\"}" --output table

# 5. Only once 3 and 4 are clean, remove the state file
rm -f terraform.tfstate terraform.tfstate.backup
```

What the destroy removes here, and what it does not:

| Resource | Destroyed by `tofu destroy`? | Note |
| --- | --- | --- |
| `oci_core_instance` | yes | its boot volume is destroyed with it (`preserve_boot_volume` defaults to false) |
| Ephemeral public IP | yes | released with the VNIC; only *reserved* IPs bill separately |
| Subnet, security list, route table, internet gateway, VCN | yes | order handled by dependency graph |
| Object Storage bucket | n/a | this project provisions none |
| Local `.terraform/`, state, `uptime-data` volume | no | local artifacts; `down -v` and `rm -rf` handle those |

## Residual risk to watch after a future deployment

- **A1 capacity**: if `apply` ever falls back to a paid shape to get past "out of
  host capacity", that instance bills after the trial credits expire. Destroy it
  or confirm the Always Free envelope (4 OCPU / 24 GB A1 total, 200 GB block
  storage) before the trial ends.
- **Billing lag**: OCI usage reports can trail by hours, so "zero in the console"
  may still show a small residual line for the current period. That is
  accounting, not a live resource.
- **State deletion**: removing `terraform.tfstate` while resources still exist
  orphans them — they keep billing and OpenTofu can no longer manage them. Always
  verify with step 3 and step 4 before deleting state.

## Readiness

The repository is ready for final packaging and documentation: no cloud
footprint, no local containers or images, a clean working tree, and a certified
`PORTABLE_CERTIFIED 100/100` audit. The one outstanding gap from the original
plan is that **Stage 5 (live OCI provisioning) was never executed** — there is no
public IP, no remote deployment and no live alert to report. That remains an
explicit open item rather than a completed one.
