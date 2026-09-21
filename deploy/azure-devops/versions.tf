terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    azurerm     = { source = "hashicorp/azurerm", version = "= 5.6.0" }
    azuredevops = { source = "microsoft/azuredevops", version = "= 1.16.0" }
  }
  backend "azurerm" {}
}
provider "azurerm" {
  features {}
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
}
# AZDO_ORG_SERVICE_URL and AZDO_PERSONAL_ACCESS_TOKEN come from the operator environment.
provider "azuredevops" {}
data "azurerm_client_config" "current" {}
