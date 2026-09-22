mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "00000000-0000-0000-0000-000000000003" }
  }
}
variables {
  subscription_id           = "00000000-0000-0000-0000-000000000001"
  name                      = "scraperci123"
  secret_operator_object_id = "00000000-0000-0000-0000-000000000002"
}
run "disabled" {
  command = plan
  assert {
    condition     = length(azurerm_container_app_job.research) == 0 && length(azurerm_storage_account.research) == 0
    error_message = "Agent infrastructure must be opt-in."
  }
}
run "agent_foundation" {
  command = plan
  variables {
    enable_research_agent = true
  }
  assert {
    condition     = length(azurerm_container_app_job.research) == 0 && length(azurerm_storage_account.research) == 1
    error_message = "Do not deploy job before images and secrets exist."
  }
}
run "agent_workload" {
  command = plan
  variables {
    enable_research_agent = true
    deploy_workloads      = true
  }
  assert {
    condition     = azurerm_container_app_job.research[0].replica_retry_limit == 0 && azurerm_container_app_job.research[0].replica_timeout_in_seconds == 300 && azurerm_container_app_job.research[0].manual_trigger_config[0].parallelism == 1
    error_message = "No paid platform retry or unbounded parallelism."
  }
  assert {
    condition     = azurerm_storage_account.research[0].public_network_access == "Disabled" && !azurerm_storage_account.research[0].shared_access_key_enabled && !azurerm_storage_account.research[0].allow_nested_items_to_be_public
    error_message = "Reports must use private storage and Entra authentication."
  }
  assert {
    condition     = toset(keys(azurerm_role_assignment.agent_secrets)) == toset(["mcp-api-key", "openai-api-key"]) && alltrue([for s in azurerm_container_app_job.research[0].secret : s.value == null || s.value == ""])
    error_message = "Agent may read only its two referenced credentials."
  }
  assert {
    condition     = alltrue([for s in azurerm_container_app.backend[0].secret : s.name != "openai-api-key"])
    error_message = "Browser must never receive the model key."
  }
}

