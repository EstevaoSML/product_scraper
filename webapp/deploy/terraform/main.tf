resource "azurerm_resource_group" "main" {
  name     = "rg-${var.name}"
  location = var.location
  tags     = var.tags
}

resource "azurerm_user_assigned_identity" "backend" {
  name                = "id-${var.name}-browser"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}
resource "azurerm_container_registry" "main" {
  name                   = "${var.name}acr"
  resource_group_name    = azurerm_resource_group.main.name
  location               = var.location
  sku                    = "Basic"
  admin_enabled          = false
  anonymous_pull_enabled = false
  tags                   = var.tags
}
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.backend.principal_id
}
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${var.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
}
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${var.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  address_space       = ["10.40.0.0/16"]
}
resource "azurerm_subnet" "containers" {
  name                 = "containers"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.40.2.0/23"]
  delegation {
    name = "containers"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}
resource "azurerm_subnet" "endpoints" {
  name                 = "private-endpoints"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.40.4.0/24"]
}
resource "azurerm_key_vault" "main" {
  name                          = "kv-${var.name}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = var.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = length(var.operator_ipv4_cidrs) > 0
  network_acls {
    default_action = "Deny"
    bypass         = "None"
    ip_rules       = var.operator_ipv4_cidrs
  }
  tags = var.tags
}
resource "azurerm_role_assignment" "secret_operator" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.secret_operator_object_id
}
# Secret values are deliberately absent from Terraform resources/data/state.
resource "azurerm_role_assignment" "backend_secret" {
  count                = var.deploy_workloads ? 1 : 0
  scope                = "${azurerm_key_vault.main.id}/secrets/scraper-api-key"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.backend.principal_id
}
locals {
  private_services = {
    blob  = { zone = "privatelink.blob.core.windows.net", id = azurerm_storage_account.catalog.id, group = "blob" }
    dfs   = { zone = "privatelink.dfs.core.windows.net", id = azurerm_storage_account.catalog.id, group = "dfs" }
    vault = { zone = "privatelink.vaultcore.azure.net", id = azurerm_key_vault.main.id, group = "vault" }
  }
}
resource "azurerm_private_dns_zone" "services" {
  for_each            = local.private_services
  name                = each.value.zone
  resource_group_name = azurerm_resource_group.main.name
}
resource "azurerm_private_dns_zone_virtual_network_link" "services" {
  for_each            = local.private_services
  name                = "${each.key}-vnet"
  private_dns_zone_id = azurerm_private_dns_zone.services[each.key].id
  virtual_network_id  = azurerm_virtual_network.main.id
}
resource "azurerm_private_endpoint" "services" {
  for_each            = local.private_services
  name                = "pe-${var.name}-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  subnet_id           = azurerm_subnet.endpoints.id
  private_service_connection {
    name                           = each.key
    private_connection_resource_id = each.value.id
    subresource_names              = [each.value.group]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.services[each.key].id]
  }
}
resource "azurerm_container_app_environment" "main" {
  logs_destination               = "log-analytics"
  name                           = "cae-${var.name}"
  resource_group_name            = azurerm_resource_group.main.name
  location                       = var.location
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id       = azurerm_subnet.containers.id
  internal_load_balancer_enabled = false
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}
resource "azurerm_monitor_diagnostic_setting" "vault" {
  name                       = "vault-audit"
  target_resource_id         = azurerm_key_vault.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  enabled_log {
    category_group = "audit"
  }
}

resource "azurerm_user_assigned_identity" "api" {
  name                = "id-${var.name}-api"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}
resource "azurerm_role_assignment" "api_acr" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}
resource "azurerm_role_assignment" "api_secrets" {
  for_each             = var.deploy_workloads ? toset(["mcp-api-key", "scraper-api-key"]) : toset([])
  scope                = "${azurerm_key_vault.main.id}/secrets/${each.value}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}
