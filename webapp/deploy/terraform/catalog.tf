variable "deploy_web" {
  description = "Enable the website after the initial seed job has completed."
  type        = bool
  default     = true
}
variable "web_image_tag" {
  type    = string
  default = "v1"
}
variable "worker_image_tag" {
  type    = string
  default = "v1"
}
variable "suggestion_email" {
  type    = string
  default = ""
}
variable "collection_cron" {
  description = "UTC cron. Empty means manual collection; avoids unrequested recurring OpenAI charges."
  type        = string
  default     = ""
}

resource "azurerm_storage_account" "catalog" {
  name                            = "${var.name}lake"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  public_network_access           = "Disabled"
  blob_properties {
    delete_retention_policy { days = 30 }
    container_delete_retention_policy { days = 30 }
  }
  lifecycle { prevent_destroy = true }
  tags = var.tags
}

# ARM-managed container: no data-plane access from the deployment workstation.
# With hierarchical namespace, this is also an ADLS Gen2 filesystem.
resource "azurerm_storage_container" "catalog" {
  name                  = "catalog"
  storage_account_id    = azurerm_storage_account.catalog.id
  container_access_type = "private"
  lifecycle { prevent_destroy = true }
}

resource "azurerm_user_assigned_identity" "catalog" {
  for_each            = toset(["web", "worker"])
  name                = "id-${var.name}-${each.key}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
}
resource "azurerm_role_assignment" "catalog_acr" {
  for_each             = azurerm_user_assigned_identity.catalog
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = each.value.principal_id
}
resource "azurerm_role_assignment" "catalog_data" {
  for_each             = { web = "Storage Blob Data Reader", worker = "Storage Blob Data Contributor" }
  scope                = azurerm_storage_container.catalog.id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.catalog[each.key].principal_id
}
resource "azurerm_role_assignment" "worker_secrets" {
  for_each             = var.deploy_workloads ? toset(["openai-api-key", "mcp-api-key"]) : toset([])
  scope                = "${azurerm_key_vault.main.id}/secrets/${each.value}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.catalog["worker"].principal_id
}

resource "azurerm_container_app" "web" {
  count                        = var.deploy_workloads && var.deploy_web ? 1 : 0
  name                         = "${var.name}-web"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.catalog["web"].id]
  }
  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.catalog["web"].id
  }
  ingress {
    external_enabled           = true
    target_port                = 8000
    transport                  = "http"
    allow_insecure_connections = false
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  template {
    min_replicas = 1
    max_replicas = 3
    http_scale_rule {
      name                = "http"
      concurrent_requests = "50"
    }
    container {
      name   = "web"
      image  = "${azurerm_container_registry.main.login_server}/preco-claro:${var.web_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
      dynamic "env" {
        for_each = {
          CATALOG_BACKEND         = "azure"
          CATALOG_STORAGE_ACCOUNT = azurerm_storage_account.catalog.name
          AZURE_CLIENT_ID         = azurerm_user_assigned_identity.catalog["web"].client_id
          SUGGESTION_EMAIL        = var.suggestion_email
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/healthz"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/readyz"
        timeout   = 30
      }
    }
  }
  depends_on = [azurerm_role_assignment.catalog_acr, azurerm_role_assignment.catalog_data,
  azurerm_private_endpoint.services, azurerm_private_dns_zone_virtual_network_link.services]
}

resource "azurerm_container_app_job" "catalog" {
  for_each                     = var.deploy_workloads ? toset(["seed", "collect"]) : toset([])
  name                         = "${var.name}-${each.key}"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 2400
  replica_retry_limit          = 0
  dynamic "manual_trigger_config" {
    for_each = each.key == "seed" || var.collection_cron == "" ? [1] : []
    content {
      parallelism              = 1
      replica_completion_count = 1
    }
  }
  dynamic "schedule_trigger_config" {
    for_each = each.key == "collect" && var.collection_cron != "" ? [1] : []
    content {
      cron_expression          = var.collection_cron
      parallelism              = 1
      replica_completion_count = 1
    }
  }
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.catalog["worker"].id]
  }
  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.catalog["worker"].id
  }
  dynamic "secret" {
    for_each = each.key == "collect" ? toset(["openai-api-key", "mcp-api-key"]) : toset([])
    content {
      name                = secret.value
      identity            = azurerm_user_assigned_identity.catalog["worker"].id
      key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/${secret.value}"
    }
  }
  template {
    container {
      name   = "catalog"
      image  = "${azurerm_container_registry.main.login_server}/preco-claro-worker:${var.worker_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
      args   = [each.key, "--max-cost-usd", "0.05", "--image-max-cost-usd", "0"]
      dynamic "env" {
        for_each = {
          CATALOG_STORAGE_ACCOUNT = azurerm_storage_account.catalog.name
          AZURE_CLIENT_ID         = azurerm_user_assigned_identity.catalog["worker"].client_id
          MCP_ENDPOINT            = "https://${azurerm_container_app.api[0].ingress[0].fqdn}/mcp"
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = each.key == "collect" ? { OPENAI_API_KEY = "openai-api-key", MCP_API_KEY = "mcp-api-key" } : {}
        content {
          name        = env.key
          secret_name = env.value
        }
      }
    }
  }
  depends_on = [azurerm_role_assignment.catalog_acr, azurerm_role_assignment.catalog_data,
    azurerm_role_assignment.worker_secrets, azurerm_private_endpoint.services,
  azurerm_private_dns_zone_virtual_network_link.services]
}
