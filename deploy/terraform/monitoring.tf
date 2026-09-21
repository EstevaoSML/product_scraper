# Container Apps sends stdout/stderr and system logs to this workspace through
# the environment's log_analytics_workspace_id. Do not configure a duplicate sink.
resource "azurerm_log_analytics_saved_search" "errors" {
  name                       = "scraper-errors"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  category                   = "Retail scraper"
  display_name               = "Structured scraper errors"
  query                      = <<-KQL
    ContainerAppConsoleLogs_CL
    | where TimeGenerated > ago(24h)
    | where ContainerAppName_s in ("${var.name}-mcp", "${var.name}-backend")
    | extend Error = parse_json(Log_s)
    | where tostring(Error.severity) == "ERROR"
    | project TimeGenerated, ContainerAppName_s, RevisionName_s,
        RequestId=tostring(Error.request_id), Status=toint(Error.status),
        Code=tostring(Error.code), Message=tostring(Error.message), Service=tostring(Error.service)
    | order by TimeGenerated desc
  KQL
}
resource "azurerm_log_analytics_saved_search" "platform" {
  name                       = "scraper-platform-errors"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  category                   = "Retail scraper"
  display_name               = "Container platform failures"
  query                      = <<-KQL
    ContainerAppSystemLogs_CL
    | where TimeGenerated > ago(24h)
    | where ContainerAppName_s in ("${var.name}-mcp", "${var.name}-backend")
    | where Log_s has_any ("error", "failed", "unhealthy", "backoff")
    | project TimeGenerated, ContainerAppName_s, Log_s
    | order by TimeGenerated desc
  KQL
}
