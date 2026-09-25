variable "subscription_id" {
  type = string
}
variable "name" {
  description = "Globally unique lowercase letters/digits, 6-16 characters. No secrets."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{5,15}$", var.name))
    error_message = "Use 6-16 lowercase letters/digits, starting with a letter."
  }
}
variable "location" {
  type    = string
  default = "brazilsouth"
}
variable "deploy_workloads" {
  description = "Phase 1 false: provision foundation. Set true only after images and secrets exist."
  type        = bool
  default     = false
}
variable "api_image_tag" {
  type    = string
  default = "v1"
}
variable "browser_image_tag" {
  type    = string
  default = "v1"
}
variable "operator_ipv4_cidrs" {
  description = "Optional temporary public /32 addresses for Key Vault secret provisioning. Empty means private endpoint access only."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for ip in var.operator_ipv4_cidrs : can(cidrhost(ip, 0)) && endswith(ip, "/32")])
    error_message = "Use explicit /32 operator addresses, never an open range."
  }
}
variable "secret_operator_object_id" {
  description = "Entra object ID of the administrator who seeds/rotates secrets. Not an application/client ID."
  type        = string
}
variable "log_retention_days" {
  type    = number
  default = 30
}
variable "tags" {
  type    = map(string)
  default = { project = "retail-scraper", managed_by = "terraform" }
}
