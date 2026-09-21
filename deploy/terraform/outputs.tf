output "resource_group_name" {
  value = azurerm_resource_group.main.name
}
output "registry_name" {
  value = azurerm_container_registry.main.name
}
output "key_vault_name" {
  value = azurerm_key_vault.main.name
}
output "api_name" {
  value = var.deploy_workloads ? azurerm_container_app.api[0].name : null
}
output "mcp_endpoint" {
  value = var.deploy_workloads ? "https://${azurerm_container_app.api[0].ingress[0].fqdn}/mcp" : null
}
output "backend_name" {
  value = var.deploy_workloads ? azurerm_container_app.backend[0].name : null
}
output "log_workspace_id" {
  value = azurerm_log_analytics_workspace.main.workspace_id
}
