mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "33333333-3333-3333-3333-333333333333"
      object_id = "22222222-2222-2222-2222-222222222222"
    }
  }
}
variables {
  deploy_job = false
  enable_monthly_schedule = false
  package_sha256 = ""
  subscription_id   = "11111111-1111-1111-1111-111111111111"
  name              = "pricefixture01"
  alert_email       = "operator@example.com"
  budget_start_date = "2026-09-01T00:00:00Z"
}
run "foundation" {
  command = plan
  assert {
    condition = one([for rule in azurerm_storage_management_policy.retention.rule : rule.actions[0].base_blob[0].delete_after_days_since_modification_greater_than if rule.name == "expire-job-diagnostics"]) == 30
    error_message = "Private job diagnostics must expire after 30 days."
  }
  assert {
    condition     = azurerm_container_app_environment.jobs.location == var.location && azurerm_container_app_environment.jobs.name == "cae-${var.name}"
    error_message = "Existing deployment location and name must remain unchanged without an override."
  }
  assert {
    condition     = length(azurerm_container_app_job.monthly) == 0
    error_message = "Do not start a job before its private package is uploaded."
  }
  assert {
    condition     = azurerm_storage_account.data.is_hns_enabled && !azurerm_storage_account.data.shared_access_key_enabled && !azurerm_storage_account.data.allow_nested_items_to_be_public
    error_message = "Private Data Lake data must require Entra authentication."
  }
  assert {
    condition     = alltrue([for c in azurerm_storage_container.private : c.container_access_type == "private"])
    error_message = "Packages and raw reports cannot be public containers."
  }
}
run "independent_compute_region" {
  command = plan
  variables {
    location         = "eastus"
    compute_location = "eastus2"
    deploy_job       = true
    package_sha256   = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = azurerm_container_app_environment.jobs.location == "eastus2" && azurerm_container_app_job.monthly[0].location == "eastus2" && azurerm_container_app_environment.jobs.name == "cae-${var.name}-eastus2"
    error_message = "Override must move both compute resources and avoid the failed environment name."
  }
  assert {
    condition     = azurerm_storage_account.data.location == "eastus" && azurerm_key_vault.secrets.location == "eastus" && azurerm_user_assigned_identity.job.location == "eastus" && azurerm_resource_group.portfolio.location == "eastus"
    error_message = "A capacity workaround must not move or replace durable resources."
  }
}
run "manual_first_test" {
  command = plan
  variables {
    deploy_job     = true
    package_sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = azurerm_container_app_job.monthly[0].replica_retry_limit == 0 && azurerm_container_app_job.monthly[0].replica_timeout_in_seconds == 14400 && length(azurerm_container_app_job.monthly[0].manual_trigger_config) == 1
    error_message = "The first deployment must be manual, with no paid retry and bounded compute."
  }
  assert {
    condition     = startswith(azurerm_container_app_job.monthly[0].template[0].container[0].image, "mcr.microsoft.com/playwright/python@sha256:") && length(azurerm_container_app_job.monthly[0].registry) == 0 && length(azurerm_container_app_job.monthly[0].secret) == 0
    error_message = "Use Microsoft public base image, no ACR credentials or raw secrets."
  }
  assert {
    condition     = one([for env in azurerm_container_app_job.monthly[0].template[0].container[0].env : env.value if env.name == "RUN_MODE"]) == "smoke"
    error_message = "The first execution must make no model calls."
  }
}
run "monthly_only" {
  command = plan
  variables {
    deploy_job              = true
    enable_monthly_schedule = true
    package_sha256          = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = azurerm_container_app_job.monthly[0].schedule_trigger_config[0].cron_expression == "0 9 1 * *" && azurerm_container_app_job.monthly[0].schedule_trigger_config[0].parallelism == 1
    error_message = "Collect sequentially on the first day of the month, not daily."
  }
}
run "reject_unbuilt_package" {
  command = plan
  variables { deploy_job = true }
  expect_failures = [azurerm_container_app_job.monthly]
}
