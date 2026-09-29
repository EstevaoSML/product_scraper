terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "5.6.0"
    }
  }
}
locals {
  settings = jsondecode(file("${path.module}/../../portfolio-deployment.local.json"))
}
provider "azurerm" {
  features {}
  subscription_id                 = local.settings.subscription_id
  storage_use_azuread             = true
  resource_provider_registrations = "none"
}
variable "github_repository" {
  type        = string
  description = "Exact case-sensitive GitHub owner/repository."
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use owner/repository."
  }
}
data "azurerm_client_config" "operator" {}
data "azurerm_resource_group" "app" { name = "rg-${local.settings.name}-portfolio" }
data "azurerm_storage_account" "app" {
  name                = "st${local.settings.name}"
  resource_group_name = data.azurerm_resource_group.app.name
}
resource "azurerm_resource_group" "github" {
  name     = "rg-${local.settings.name}-github"
  location = local.settings.location
}
resource "azurerm_user_assigned_identity" "github" {
  name                = "id-${local.settings.name}-github"
  location            = azurerm_resource_group.github.location
  resource_group_name = azurerm_resource_group.github.name
}
resource "azurerm_federated_identity_credential" "github" {
  name                      = "github-portfolio-production"
  user_assigned_identity_id = azurerm_user_assigned_identity.github.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = "repo:${var.github_repository}:environment:portfolio-production"
}
resource "azurerm_storage_container" "state" {
  name                  = "github-tfstate"
  storage_account_id    = data.azurerm_storage_account.app.id
  container_access_type = "private"
  lifecycle { prevent_destroy = true }
}
resource "azurerm_role_assignment" "deployment" {
  scope                = data.azurerm_resource_group.app.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
}
# Refreshing ADLS/static website resources needs data-plane access. This also
# covers package uploads and state leases. No Key Vault secret access is granted.
resource "azurerm_role_assignment" "data" {
  scope                = data.azurerm_storage_account.app.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
}
output "client_id" { value = azurerm_user_assigned_identity.github.client_id }
output "tenant_id" { value = data.azurerm_client_config.operator.tenant_id }
output "state_account" { value = data.azurerm_storage_account.app.name }

