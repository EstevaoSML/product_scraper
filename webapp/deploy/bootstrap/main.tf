terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.6.0"
    }
  }
}
variable "subscription_id" { type = string }
variable "name" { type = string }
variable "location" {
  type    = string
  default = "brazilsouth"
}
variable "operator_object_id" { type = string }
variable "operator_ipv4" { type = string }
provider "azurerm" {
  features {}
  subscription_id                 = var.subscription_id
  storage_use_azuread             = true
  resource_provider_registrations = "none"
}
resource "azurerm_resource_group" "state" {
  name     = "rg-${var.name}-state"
  location = var.location
}
resource "azurerm_storage_account" "state" {
  name                            = "${var.name}tfstate"
  resource_group_name             = azurerm_resource_group.state.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  network_rules {
    default_action = "Deny"
    ip_rules       = [var.operator_ipv4]
    bypass         = ["None"]
  }
  blob_properties {
    versioning_enabled = true
    delete_retention_policy { days = 30 }
    container_delete_retention_policy { days = 30 }
  }
  lifecycle { prevent_destroy = true }
}
resource "azurerm_storage_container" "state" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
  lifecycle { prevent_destroy = true }
}
resource "azurerm_role_assignment" "state" {
  scope                = azurerm_storage_container.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.operator_object_id
}
output "storage_account_name" { value = azurerm_storage_account.state.name }
