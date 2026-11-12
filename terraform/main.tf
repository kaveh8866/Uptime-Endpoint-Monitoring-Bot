# Wires the network and compute modules together.
#
# PORTABILITY (AWS / GCP):
#   OCI VCN + IGW + route table + security list + subnet
#     ≈ AWS  VPC + internet gateway + route table + security group + subnet
#     ≈ GCP  VPC network + default internet route + firewall rule + subnetwork
#   OCI oci_core_instance (VM.Standard.A1.Flex)
#     ≈ AWS  aws_instance (t4g.small / t3.micro free tier)
#     ≈ GCP  google_compute_instance (e2-micro / e2-small free tier)
#
# The ingress contract is SSH only on every provider, and the instance only needs
# *outbound* connectivity for probes and alerts. That is why no load balancer,
# no public HTTP port and no bucket appear anywhere in this configuration.

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = merge(var.freeform_tags, {
    environment = var.environment
    managed_by  = "opentofu"
    stage       = "2-iac"
  })

  # Cloud-init only prepares the host: Docker, the Compose plugin and an empty
  # application directory. It carries no credential, no repository URL and no
  # endpoint list — the stack and its config are deployed over SSH (Stage 3).
  user_data = base64encode(
    templatefile("${path.module}/cloud-init.yaml.tftpl", {
      project_name = var.project_name
    })
  )
}

module "network" {
  source = "./modules/network"

  # Pin the provider address explicitly. Without this, OpenTofu infers
  # hashicorp/oci for the child module while the root uses oracle/oci, and the
  # two addresses do not share configuration ("did not find a proper
  # configuration for tenancy").
  providers = { oci = oci }

  compartment_id   = var.compartment_ocid
  name_prefix      = local.name_prefix
  vcn_cidr         = var.vcn_cidr
  subnet_cidr      = var.subnet_cidr
  ssh_allowed_cidr = var.ssh_allowed_cidr
  vcn_dns_label    = "cguptime"
  subnet_dns_label = "monitor"
  freeform_tags    = local.tags
}

module "compute" {
  source = "./modules/compute"

  providers = { oci = oci }

  compartment_id         = var.compartment_ocid
  tenancy_ocid           = var.tenancy_ocid
  name_prefix            = local.name_prefix
  subnet_id              = module.network.subnet_id
  availability_domain    = var.availability_domain
  instance_shape         = var.instance_shape
  instance_ocpus         = var.instance_ocpus
  instance_memory_gbs    = var.instance_memory_gbs
  boot_volume_size_gbs   = var.boot_volume_size_gbs
  ssh_public_key         = var.ssh_public_key
  user_data              = local.user_data
  hostname_label         = var.hostname_label
  image_id               = var.image_id
  image_operating_system = var.image_operating_system
  image_os_version       = var.image_operating_system_version
  freeform_tags          = local.tags
}
