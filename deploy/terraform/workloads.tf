resource "azurerm_container_app" "backend" {
  count                        = var.deploy_workloads ? 1 : 0
  name                         = "${var.name}-backend"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.backend.id]
  }
  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.backend.id
  }
  secret {
    name                = "scraper-api-key"
    identity            = azurerm_user_assigned_identity.backend.id
    key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/scraper-api-key"
  }
  # Internal ingress: only other apps in this environment can reach Chrome.
  ingress {
    external_enabled           = false
    target_port                = 8001
    transport                  = "http"
    allow_insecure_connections = false
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  template {
    min_replicas = 1
    max_replicas = 1
    container {
      name   = "chrome"
      image  = "${azurerm_container_registry.main.login_server}/html-scraper-browser:${var.browser_image_tag}"
      cpu    = 1.25
      memory = "2.5Gi"
      env {
        name  = "LOG_SERVICE"
        value = "scraper-browser"
      }
      env {
        name        = "API_KEY"
        secret_name = "scraper-api-key"
      }
      env {
        name  = "BROWSER_PROXY"
        value = "http://localhost:8080"
      }
      liveness_probe {
        transport = "HTTP"
        port      = 8001
        path      = "/healthz"
      }
    }
    container {
      name    = "egress"
      image   = "${azurerm_container_registry.main.login_server}/html-scraper:${var.api_image_tag}"
      command = ["python", "-m", "app.egress"]
      cpu     = 0.25
      memory  = "0.5Gi"
      liveness_probe {
        transport = "TCP"
        port      = 8080
      }
    }
  }
  depends_on = [azurerm_role_assignment.acr_pull, azurerm_role_assignment.backend_secret, azurerm_private_endpoint.services, azurerm_private_dns_zone_virtual_network_link.services]
}


resource "azurerm_container_app" "api" {
  count                        = var.deploy_workloads ? 1 : 0
  name                         = "${var.name}-mcp"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }
  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.api.id
  }
  dynamic "secret" {
    for_each = toset(["mcp-api-key", "scraper-api-key"])
    content {
      name                = secret.value
      identity            = azurerm_user_assigned_identity.api.id
      key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/${secret.value}"
    }
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
    max_replicas = 1
    container {
      name   = "api"
      image  = "${azurerm_container_registry.main.login_server}/html-scraper:${var.api_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
      env {
        name  = "LOG_SERVICE"
        value = "scraper-api"
      }
      env {
        name        = "API_KEY"
        secret_name = "mcp-api-key"
      }
      env {
        name        = "BROWSER_API_KEY"
        secret_name = "scraper-api-key"
      }
      env {
        name  = "BROWSER_URL"
        value = "https://${azurerm_container_app.backend[0].ingress[0].fqdn}"
      }
      env {
        name  = "URL_POLICY"
        value = "public"
      }
      env {
        name  = "MCP_ALLOWED_HOSTS"
        value = "${var.name}-mcp.${azurerm_container_app_environment.main.default_domain},${var.name}-mcp.${azurerm_container_app_environment.main.default_domain}:443,localhost:*,127.0.0.1:*"
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
      }
    }
  }
  depends_on = [azurerm_role_assignment.api_acr, azurerm_role_assignment.api_secrets,
  azurerm_private_endpoint.services, azurerm_private_dns_zone_virtual_network_link.services]
}
