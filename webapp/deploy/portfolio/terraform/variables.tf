variable "subscription_id" { type = string }
variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{5,15}$", var.name))
    error_message = "Use a globally unique name of 6-16 lowercase letters/digits, starting with a letter."
  }
}
variable "location" {
  type    = string
  default = "eastus"
}
variable "deploy_job" {
  type    = bool
  default = false
}
variable "enable_monthly_schedule" {
  type    = bool
  default = false
}
variable "package_sha256" {
  type    = string
  default = ""
}
variable "suggestion_email" {
  type    = string
  default = ""
}
variable "alert_email" {
  type = string
  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "An email address is required for cost alerts."
  }
}
variable "budget_start_date" {
  description = "First day of deployment month at UTC midnight. Keep unchanged on later applies."
  type        = string
  validation {
    condition     = can(regex("^20[0-9]{2}-[0-9]{2}-01T00:00:00Z$", var.budget_start_date))
    error_message = "Use YYYY-MM-01T00:00:00Z."
  }
}
