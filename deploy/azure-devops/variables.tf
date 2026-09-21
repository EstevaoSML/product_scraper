variable "subscription_id" {
  type = string
}
variable "subscription_name" {
  type = string
}
variable "project_name" {
  type = string
}
variable "repository_name" {
  type = string
}
variable "app_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{5,15}$", var.app_name))
    error_message = "Match the existing application Terraform name."
  }
}
variable "identity_resource_group_name" {
  type = string
}
variable "state_account_name" {
  type = string
}
variable "state_container_name" {
  type    = string
  default = "tfstate"
}
variable "state_resource_group_name" {
  type = string
}
variable "state_key" {
  type = string
}
variable "secret_operator_object_id" {
  type = string
}
variable "approver_ids" {
  type = list(string)
  validation {
    condition     = length(var.approver_ids) > 0
    error_message = "Provide at least one Azure DevOps approver identity ID."
  }
}
variable "location" {
  type    = string
  default = "brazilsouth"
}
variable "log_retention_days" {
  type    = number
  default = 30
}
