variable "enable_research_agent" {
  description = "Provision the agent foundation; deployment also requires deploy_workloads."
  type        = bool
  default     = false
}
variable "agent_image_tag" {
  type    = string
  default = "v1"
}

resource "azurerm_user_assigned_identity" "agent" {
  count               = var.enable_research_agent ? 1 : 0
  name                = "id-${var.name}-agent"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}
resource "azurerm_role_assignment" "agent_acr" {
  count                = var.enable_research_agent ? 1 : 0
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.agent[0].principal_id
}
resource "azurerm_role_assignment" "agent_secrets" {
  for_each             = var.enable_research_agent && var.deploy_workloads ? toset(["mcp-api-key", "openai-api-key"]) : toset([])
  scope                = "${azurerm_key_vault.main.id}/secrets/${each.value}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.agent[0].principal_id
}
resource "azurerm_storage_account" "research" {
  count                           = var.enable_research_agent ? 1 : 0
  name                            = "${var.name}research"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  public_network_access           = "Disabled"
  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }
  tags = var.tags
}
# ARM container resource: no data-plane account keys in Terraform state.
resource "azurerm_storage_container" "research" {
  count                 = var.enable_research_agent ? 1 : 0
  name                  = "research"
  storage_account_id    = azurerm_storage_account.research[0].id
  container_access_type = "private"
}
resource "azurerm_role_assignment" "agent_blobs" {
  count                = var.enable_research_agent ? 1 : 0
  scope                = "${azurerm_storage_account.research[0].id}/blobServices/default/containers/research"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.agent[0].principal_id
}
resource "azurerm_private_dns_zone" "research" {
  count               = var.enable_research_agent ? 1 : 0
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.main.name
}
resource "azurerm_private_dns_zone_virtual_network_link" "research" {
  count               = var.enable_research_agent ? 1 : 0
  name                = "research-vnet"
  private_dns_zone_id = azurerm_private_dns_zone.research[0].id
  virtual_network_id  = azurerm_virtual_network.main.id
}
resource "azurerm_private_endpoint" "research" {
  count               = var.enable_research_agent ? 1 : 0
  name                = "pe-${var.name}-research"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  subnet_id           = azurerm_subnet.endpoints.id
  private_service_connection {
    name                           = "research"
    private_connection_resource_id = azurerm_storage_account.research[0].id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.research[0].id]
  }
}
resource "azurerm_container_app_job" "research" {
  count                        = var.enable_research_agent && var.deploy_workloads ? 1 : 0
  name                         = "${var.name}-research"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 300
  replica_retry_limit          = 0
  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.agent[0].id]
  }
  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.agent[0].id
  }
  dynamic "secret" {
    for_each = toset(["mcp-api-key", "openai-api-key"])
    content {
      name                = secret.value
      identity            = azurerm_user_assigned_identity.agent[0].id
      key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/${secret.value}"
    }
  }
  template {
    container {
      name   = "agent"
      image  = "${azurerm_container_registry.main.login_server}/retail-agent:${var.agent_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
      # A bare start is inert; callers must provide explicit task and spending cap.
      args = ["--help"]
      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-api-key"
      }
      env {
        name        = "MCP_API_KEY"
        secret_name = "mcp-api-key"
      }
      env {
        name  = "MCP_ENDPOINT"
        value = "https://${azurerm_container_app.api[0].ingress[0].fqdn}/mcp"
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.agent[0].client_id
      }
      env {
        name  = "RESEARCH_STORAGE_ACCOUNT"
        value = azurerm_storage_account.research[0].name
      }
    }
  }
  depends_on = [azurerm_role_assignment.agent_acr, azurerm_role_assignment.agent_secrets,
    azurerm_role_assignment.agent_blobs, azurerm_private_endpoint.research,
    azurerm_private_dns_zone_virtual_network_link.research,
  azurerm_private_endpoint.services, azurerm_private_dns_zone_virtual_network_link.services]
}
output "research_job_name" {
  value = var.enable_research_agent && var.deploy_workloads ? azurerm_container_app_job.research[0].name : null
}
output "research_storage_account" {
  value = var.enable_research_agent ? azurerm_storage_account.research[0].name : null
}

