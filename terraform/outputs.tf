# Outputs an operator (or the next agent) needs to reach the host and deploy the
# stack. Everything here is public information: IPs and OCIDs, never secrets.

output "instance_public_ip" {
  description = "Public IPv4 address of the monitoring host. This is the address you SSH to."
  value       = module.compute.public_ip
}

output "instance_private_ip" {
  description = "Private IPv4 address inside the VCN."
  value       = module.compute.private_ip
}

output "instance_id" {
  description = "OCID of the compute instance."
  value       = module.compute.instance_id
}

output "instance_shape" {
  description = "Shape actually used, including the flex sizing in the human-readable form."
  value       = "${var.instance_shape} (${var.instance_ocpus} OCPU / ${var.instance_memory_gbs} GB)"
}

output "availability_domain" {
  description = "Availability domain the instance landed in (pin it in tfvars if A1 capacity is tight)."
  value       = module.compute.availability_domain
}

output "vcn_id" {
  description = "OCID of the VCN."
  value       = module.network.vcn_id
}

output "subnet_id" {
  description = "OCID of the public subnet."
  value       = module.network.subnet_id
}

output "security_list_id" {
  description = "OCID of the security list (ingress: 22/tcp only)."
  value       = module.network.security_list_id
}

output "connection_endpoints" {
  description = "Copy-paste connection strings for the operator."
  value = {
    ssh = "ssh ubuntu@${module.compute.public_ip}"
    # The bot has no listening port, so there is no tunnel and no URL to open.
    # Alerts are outbound only; verify the service through its container logs.
    logs = "ssh ubuntu@${module.compute.public_ip} 'cd uptime-endpoint-monitoring-bot && docker compose -f docker/docker-compose.yml logs -f uptime-bot'"
  }
}

output "deploy_next_step" {
  description = "What to run once apply finishes."
  value       = <<-EOT
    1. ssh ubuntu@${module.compute.public_ip}
    2. git clone https://github.com/kaveh8866/Uptime-Endpoint-Monitoring-Bot.git
    3. cd Uptime-Endpoint-Monitoring-Bot && cp .env.example .env && edit channels
    4. docker compose --env-file .env -f docker/docker-compose.yml up -d --build
    5. docker compose -f docker/docker-compose.yml logs -f uptime-bot
  EOT
}
