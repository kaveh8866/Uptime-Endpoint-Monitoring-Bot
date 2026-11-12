# Offline posture tests — no OCI credentials, no network, no spend.
#
# `mock_provider` replaces the OCI provider, so the plan is evaluated for real
# wiring while data sources return the mocked values below. These assertions are
# the machine-checkable form of the security claim in modules/network/main.tf:
# the host accepts SSH and nothing else.
#
#   tofu init -backend=false && tofu test

mock_provider "oci" {
  mock_data "oci_identity_availability_domains" {
    defaults = {
      # Only computed fields may be overridden; compartment_id is configured.
      availability_domains = [{
        compartment_id = "ocid1.tenancy.oc1..mocktenancy"
        id             = "ocid1.availabilitydomain.oc1..mockad"
        name           = "MOCK-AD-1"
      }]
    }
  }

}

variables {
  tenancy_ocid     = "ocid1.tenancy.oc1..mocktenancy"
  compartment_ocid = "ocid1.compartment.oc1..mockcompartment"
  region           = "eu-frankfurt-1"
  ssh_public_key   = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKeyForPlanOnly"
  # Pinning the image keeps the image data source out of the plan, so the test
  # does not have to reproduce that data source's full attribute schema.
  image_id = "ocid1.image.oc1..mockimage"
}

run "ingress_is_ssh_only" {
  command = plan

  assert {
    condition     = module.network.ingress_ports == [22]
    error_message = "The security list must expose SSH (22) and no other port."
  }
}

run "egress_allows_outbound_monitoring" {
  command = plan

  assert {
    condition     = module.network.egress_cidrs == ["0.0.0.0/0"]
    error_message = "Egress must stay open so probes and alert delivery can reach the internet."
  }
}

run "network_has_public_path" {
  command = plan

  assert {
    condition     = module.network.vcn_id != null && module.network.subnet_id != null
    error_message = "The VCN and public subnet must both be created."
  }

  assert {
    condition     = module.network.security_list_id != null && module.network.route_table_id != null
    error_message = "The subnet must be attached to a security list and a route table."
  }

  assert {
    condition     = module.compute.is_publicly_reachable
    error_message = "The instance must receive a public IP so the operator can SSH in."
  }
}

run "instance_is_small_arm_and_gets_bootstrap" {
  command = plan

  assert {
    condition     = module.compute.shape == "VM.Standard.A1.Flex"
    error_message = "Default shape must be the Always Free Ampere shape."
  }

  assert {
    condition     = module.compute.sizing.ocpus == 1 && module.compute.sizing.memory_in_gbs == 1
    error_message = "Flex sizing must stay at 1 OCPU / 1 GB for the monitoring workload."
  }

  assert {
    condition     = output.instance_shape == "VM.Standard.A1.Flex (1 OCPU / 1 GB)"
    error_message = "The shape output must report the effective sizing."
  }
}
