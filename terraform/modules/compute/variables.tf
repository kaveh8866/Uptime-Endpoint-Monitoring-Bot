variable "compartment_id" {
  description = "Compartment that owns the instance."
  type        = string
}

variable "tenancy_ocid" {
  description = "Tenancy OCID, used to list availability domains."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource display name."
  type        = string
}

variable "subnet_id" {
  description = "Subnet the primary VNIC attaches to."
  type        = string
}

variable "availability_domain" {
  description = "Availability domain name. Leave null to use the first AD in the region."
  type        = string
  default     = null
}

variable "instance_shape" {
  description = "Compute shape (VM.Standard.A1.Flex is Always Free eligible)."
  type        = string

  validation {
    condition     = can(regex("^VM\\.Standard\\.", var.instance_shape))
    error_message = "instance_shape must be an OCI compute shape such as VM.Standard.A1.Flex."
  }
}

variable "instance_ocpus" {
  description = "OCPUs for flex shapes."
  type        = number
  default     = 1
}

variable "instance_memory_gbs" {
  description = "Memory in GB for flex shapes."
  type        = number
  default     = 1
}

variable "boot_volume_size_gbs" {
  description = "Boot volume size in GB."
  type        = number
  default     = 50
}

variable "ssh_public_key" {
  description = "OpenSSH public key content injected into instance metadata."
  type        = string

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) ", var.ssh_public_key))
    error_message = "ssh_public_key must be an OpenSSH public key, never a private key."
  }
}

variable "user_data" {
  description = "Base64-encoded cloud-init passed as instance metadata."
  type        = string
}

variable "hostname_label" {
  description = "DNS label for the primary VNIC."
  type        = string
  default     = "uptime-bot"
}

variable "image_id" {
  description = "Explicit image OCID. Leave null to resolve the latest matching Ubuntu image."
  type        = string
  default     = null
}

variable "image_operating_system" {
  description = "Operating system used when image_id is null."
  type        = string
  default     = "Canonical Ubuntu"
}

variable "image_os_version" {
  description = "OS version used when image_id is null."
  type        = string
  default     = "24.04"
}

variable "freeform_tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
