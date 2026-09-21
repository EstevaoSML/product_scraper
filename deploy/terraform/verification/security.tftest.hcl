mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "00000000-0000-0000-0000-000000000003"
    }
  }
}

variables {
  subscription_id           = "00000000-0000-0000-0000-000000000001"
  name                      = "scraperci123"
  secret_operator_object_id = "00000000-0000-0000-0000-000000000002"
}

run "foundation_only" {
  command = plan
  assert {
    condition     = length(azurerm_container_app.api) == 0 && length(azurerm_container_app.backend) == 0
    error_message = "Foundation phase must not deploy workloads before images and secrets exist."
  }
  assert {
    condition     = azurerm_key_vault.main.purge_protection_enabled && !azurerm_key_vault.main.public_network_access_enabled
    error_message = "Key Vault must be protected and private by default."
  }
  assert {
    condition     = !azurerm_container_app_environment.main.internal_load_balancer_enabled
    error_message = "Environment must allow the public API; browser ingress stays internal."
  }
}

run "workloads" {
  command = plan
  variables {
    deploy_workloads = true
  }
  assert {
    condition     = azurerm_container_app.api[0].ingress[0].external_enabled && !azurerm_container_app.api[0].ingress[0].allow_insecure_connections && !azurerm_container_app.backend[0].ingress[0].external_enabled
    error_message = "Only the API may expose public HTTPS ingress."
  }
  assert {
    condition     = alltrue([for secret in azurerm_container_app.api[0].secret : secret.value == null || secret.value == ""])
    error_message = "Secrets must use Key Vault references, never plaintext values."
  }
  assert {
    condition     = azurerm_container_app.backend[0].template[0].max_replicas == 1 && azurerm_container_app.backend[0].template[0].min_replicas == 1 && length(azurerm_role_assignment.api_secrets) == 2
    error_message = "Stateful browser must stay on one replica with separate credentials."
  }
}

run "logging" {
  command = plan
  assert {
    condition     = azurerm_container_app_environment.main.logs_destination == "log-analytics" && var.log_retention_days >= 30
    error_message = "Container console/system logs require durable Log Analytics storage."
  }
  assert {
    condition     = strcontains(azurerm_log_analytics_saved_search.errors.query, "Error.request_id") && strcontains(azurerm_log_analytics_saved_search.platform.query, "ContainerAppSystemLogs_CL")
    error_message = "Queries must cover correlated application errors and platform failures."
  }
}
