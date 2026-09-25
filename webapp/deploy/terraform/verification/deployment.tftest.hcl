mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "33333333-3333-3333-3333-333333333333" }
  }
}

variables {
  subscription_id           = "11111111-1111-1111-1111-111111111111"
  name                      = "precotest123"
  secret_operator_object_id = "22222222-2222-2222-2222-222222222222"
}

run "foundation" {
  command = plan
  assert {
    condition     = azurerm_storage_account.catalog.is_hns_enabled && azurerm_storage_account.catalog.public_network_access == "Disabled" && !azurerm_storage_account.catalog.shared_access_key_enabled
    error_message = "Data Lake must use HNS, private networking and identity authentication."
  }
  assert {
    condition     = length(azurerm_private_endpoint.services) == 3
    error_message = "Private endpoints for vault, blob and dfs are required."
  }
  assert {
    condition     = length(azurerm_container_app.web) == 0 && length(azurerm_container_app_job.catalog) == 0
    error_message = "Foundation must not start unbuilt images."
  }
}

run "workloads" {
  command = plan
  variables { deploy_workloads = true }
  assert {
    condition     = azurerm_container_app.web[0].ingress[0].external_enabled && !azurerm_container_app.web[0].ingress[0].allow_insecure_connections && !azurerm_container_app.api[0].ingress[0].external_enabled && !azurerm_container_app.backend[0].ingress[0].external_enabled
    error_message = "Only the HTTPS website may have public ingress."
  }
  assert {
    condition     = azurerm_role_assignment.catalog_data["web"].role_definition_name == "Storage Blob Data Reader" && length(azurerm_container_app.web[0].secret) == 0
    error_message = "Web identity must not write data or receive research secrets."
  }
  assert {
    condition     = length(azurerm_container_app_job.catalog["collect"].manual_trigger_config) == 1 && azurerm_container_app_job.catalog["collect"].replica_retry_limit == 0
    error_message = "Research must be manual with no paid automatic retries by default."
  }
  assert {
    condition     = alltrue([for s in azurerm_container_app_job.catalog["collect"].secret : s.value == null && s.key_vault_secret_id != null])
    error_message = "Secret values must be resolved from Key Vault."
  }
}

run "optional_schedule" {
  command = plan
  variables {
    deploy_workloads = true
    collection_cron  = "0 12 * * *"
  }
  assert {
    condition     = azurerm_container_app_job.catalog["collect"].schedule_trigger_config[0].cron_expression == "0 12 * * *" && length(azurerm_container_app_job.catalog["seed"].manual_trigger_config) == 1
    error_message = "Only collection may be scheduled."
  }
}

run "seed_before_public_web" {
  command = plan
  variables {
    deploy_workloads = true
    deploy_web       = false
  }
  assert {
    condition     = length(azurerm_container_app.web) == 0 && length(azurerm_container_app_job.catalog) == 2
    error_message = "Initial seed must run before the public website is enabled."
  }
}
