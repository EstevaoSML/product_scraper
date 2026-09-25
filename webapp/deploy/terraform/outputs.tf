output "resource_group" { value = azurerm_resource_group.main.name }
output "registry" { value = azurerm_container_registry.main.name }
output "vault_name" { value = azurerm_key_vault.main.name }
output "lake_account" { value = azurerm_storage_account.catalog.name }
output "web_url" { value = var.deploy_workloads && var.deploy_web ? "https://${azurerm_container_app.web[0].ingress[0].fqdn}" : null }
output "seed_job" { value = var.deploy_workloads ? azurerm_container_app_job.catalog["seed"].name : null }
output "collection_job" { value = var.deploy_workloads ? azurerm_container_app_job.catalog["collect"].name : null }
