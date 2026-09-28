terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "5.6.0"
    }
  }
  # Separate local state for the first portfolio test. Never reuse the full
  # production stack's state. README documents optional Azure state migration.
}
provider "azurerm" {
  features {}
  subscription_id     = var.subscription_id
  storage_use_azuread = true
}
data "azurerm_client_config" "operator" {}

resource "azurerm_resource_group" "portfolio" {
  name     = "rg-${var.name}-portfolio"
  location = var.location
  tags     = { project = "preco-claro-portfolio", managed_by = "terraform" }
}
resource "azurerm_storage_account" "data" {
  name                            = "st${var.name}"
  resource_group_name             = azurerm_resource_group.portfolio.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  public_network_access           = "Enabled"
}
resource "azurerm_role_assignment" "operator_data" {
  scope                = azurerm_storage_account.data.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.operator.object_id
}
resource "azurerm_storage_container" "private" {
  for_each              = toset(["catalog", "packages"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.data.id
  container_access_type = "private"
  depends_on            = [azurerm_role_assignment.operator_data]
}
resource "azurerm_storage_account_static_website" "site" {
  storage_account_id = azurerm_storage_account.data.id
  index_document     = "index.html"
  error_404_document = "404.html"
  depends_on         = [azurerm_role_assignment.operator_data]
}
resource "azurerm_storage_management_policy" "retention" {
  storage_account_id = azurerm_storage_account.data.id
  rule {
    name    = "expire-private-diagnostics"
    enabled = true
    filters {
      prefix_match = ["catalog/reports/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob { delete_after_days_since_modification_greater_than = 90 }
    }
  }
  rule {
    name    = "expire-old-snapshots"
    enabled = true
    filters {
      prefix_match = ["catalog/catalog/snapshots/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob { delete_after_days_since_modification_greater_than = 30 }
    }
  }
  # latest.json (all price history), ledgers, package and public assets are kept.
}
resource "azurerm_user_assigned_identity" "job" {
  name                = "id-${var.name}-job"
  resource_group_name = azurerm_resource_group.portfolio.name
  location            = var.location
}
resource "azurerm_role_assignment" "job_data" {
  for_each             = toset(["catalog", "$web"])
  scope                = "${azurerm_storage_account.data.id}/blobServices/default/containers/${each.key}"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.job.principal_id
  depends_on           = [azurerm_storage_container.private, azurerm_storage_account_static_website.site]
}
resource "azurerm_role_assignment" "job_package" {
  scope                = "${azurerm_storage_account.data.id}/blobServices/default/containers/packages"
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.job.principal_id
  depends_on           = [azurerm_storage_container.private]
}
resource "azurerm_key_vault" "secrets" {
  name                          = "kv-${var.name}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.portfolio.name
  tenant_id                     = data.azurerm_client_config.operator.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 7
  purge_protection_enabled      = true
  public_network_access_enabled = true
}
resource "azurerm_role_assignment" "operator_secrets" {
  scope                = azurerm_key_vault.secrets.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.operator.object_id
}
resource "azurerm_role_assignment" "job_secret" {
  # Dedicated portfolio vault; its only persistent runtime secret is OpenAI.
  # Provision the value outside Terraform after the foundation exists.
  scope                = azurerm_key_vault.secrets.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.job.principal_id
}
resource "azurerm_container_app_environment" "jobs" {
  name                = "cae-${var.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.portfolio.name
  # Azure Monitor destination, with NO diagnostic setting or Log Analytics
  # workspace: no paid log ingestion sink is configured.
  logs_destination = "azure-monitor"
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}
resource "azurerm_container_app_job" "monthly" {
  count                        = var.deploy_job ? 1 : 0
  name                         = "job-${var.name}-monthly"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.portfolio.name
  container_app_environment_id = azurerm_container_app_environment.jobs.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 14400
  replica_retry_limit          = 0
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.job.id]
  }
  dynamic "manual_trigger_config" {
    for_each = var.enable_monthly_schedule ? [] : [1]
    content {
      parallelism              = 1
      replica_completion_count = 1
    }
  }
  dynamic "schedule_trigger_config" {
    for_each = var.enable_monthly_schedule ? [1] : []
    content {
      cron_expression          = "0 9 1 * *"
      parallelism              = 1
      replica_completion_count = 1
    }
  }
  template {
    container {
      name = "portfolio"
      # Microsoft-owned public base, verified 2026-09-27. No ACR resource/login.
      image   = "mcr.microsoft.com/playwright/python@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0"
      cpu     = 2
      memory  = "4Gi"
      command = ["python3", "-c", file("${path.module}/../../../portfolio/bootstrap.py")]
      dynamic "env" {
        for_each = {
          AZURE_CLIENT_ID         = azurerm_user_assigned_identity.job.client_id
          CATALOG_STORAGE_ACCOUNT = azurerm_storage_account.data.name
          KEY_VAULT_NAME          = azurerm_key_vault.secrets.name
          PACKAGE_SHA256          = var.package_sha256
          RUN_MODE                = var.enable_monthly_schedule ? "collect" : "smoke"
          MAX_TASKS               = "40"
          SUGGESTION_EMAIL        = var.suggestion_email
        }
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  lifecycle {
    precondition {
      condition     = can(regex("^[a-f0-9]{64}$", var.package_sha256))
      error_message = "Build and upload the immutable package before enabling the job."
    }
  }
  depends_on = [azurerm_role_assignment.job_data, azurerm_role_assignment.job_package, azurerm_role_assignment.job_secret]
}
resource "azurerm_consumption_budget_resource_group" "alert" {
  name              = "portfolio-monthly"
  resource_group_id = azurerm_resource_group.portfolio.id
  amount            = 8
  time_grain        = "Monthly"
  time_period {
    start_date = var.budget_start_date
  }
  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    contact_emails = [var.alert_email]
  }
  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    contact_emails = [var.alert_email]
  }
}
