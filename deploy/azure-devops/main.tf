data "azuredevops_project" "main" { name = var.project_name }
data "azuredevops_git_repository" "main" {
  project_id = data.azuredevops_project.main.id
  name       = var.repository_name
}
data "azurerm_resource_group" "app" { name = "rg-${var.app_name}" }
data "azurerm_resource_group" "identity" { name = var.identity_resource_group_name }
data "azurerm_user_assigned_identity" "api" {
  name                = "id-${var.app_name}-api"
  resource_group_name = data.azurerm_resource_group.app.name
}
data "azurerm_user_assigned_identity" "browser" {
  name                = "id-${var.app_name}-browser"
  resource_group_name = data.azurerm_resource_group.app.name
}
resource "azurerm_user_assigned_identity" "release" {
  name                = "id-${var.app_name}-release"
  resource_group_name = data.azurerm_resource_group.identity.name
  location            = data.azurerm_resource_group.identity.location
}
resource "azuredevops_serviceendpoint_azurerm" "release" {
  project_id                             = data.azuredevops_project.main.id
  service_endpoint_name                  = "${var.app_name}-release"
  service_endpoint_authentication_scheme = "WorkloadIdentityFederation"
  credentials { serviceprincipalid = azurerm_user_assigned_identity.release.client_id }
  azurerm_spn_tenantid      = data.azurerm_client_config.current.tenant_id
  azurerm_subscription_id   = var.subscription_id
  azurerm_subscription_name = var.subscription_name
}
resource "azurerm_federated_identity_credential" "release" {
  name                      = "azure-devops"
  user_assigned_identity_id = azurerm_user_assigned_identity.release.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azuredevops_serviceendpoint_azurerm.release.workload_identity_federation_issuer
  subject                   = azuredevops_serviceendpoint_azurerm.release.workload_identity_federation_subject
}
resource "azurerm_role_assignment" "deploy" {
  scope                = data.azurerm_resource_group.app.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.release.principal_id
}
resource "azurerm_role_assignment" "push" {
  scope                = "${data.azurerm_resource_group.app.id}/providers/Microsoft.ContainerRegistry/registries/${var.app_name}acr"
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.plan.principal_id
}
resource "azurerm_role_assignment" "state" {
  scope                = "/subscriptions/${var.subscription_id}/resourceGroups/${var.state_resource_group_name}/providers/Microsoft.Storage/storageAccounts/${var.state_account_name}/blobServices/default/containers/${var.state_container_name}"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.release.principal_id
}
# Terraform manages app role assignments. Delegation is limited to the intended
# runtime identities/roles and existing secret operator; never the release identity.
locals {
  runtime_principals = "${data.azurerm_user_assigned_identity.api.principal_id}, ${data.azurerm_user_assigned_identity.browser.principal_id}"
  runtime_roles      = "7f951dda-4ed3-4680-a7ca-43fe172d538d, 4633458b-17de-408a-b874-0445c86b69e6"
  officer_role       = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
}
resource "azurerm_role_assignment" "app_rbac" {
  scope                = data.azurerm_resource_group.app.id
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = azurerm_user_assigned_identity.release.principal_id
  condition_version    = "2.0"
  condition            = <<-CONDITION
    (
      (!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'}))
      OR (
        (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.runtime_roles}}
         AND @Request[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${local.runtime_principals}})
        OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.officer_role}}
            AND @Request[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${var.secret_operator_object_id}})
      )
    ) AND (
      (!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'}))
      OR (
        (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.runtime_roles}}
         AND @Resource[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${local.runtime_principals}})
        OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.officer_role}}
            AND @Resource[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${var.secret_operator_object_id}})
      )
    )
  CONDITION
}
resource "azuredevops_build_definition" "ci" {
  project_id              = data.azuredevops_project.main.id
  name                    = "${var.app_name}-CI"
  job_authorization_scope = "project"
  ci_trigger { use_yaml = true }
  repository {
    repo_type   = "TfsGit"
    repo_id     = data.azuredevops_git_repository.main.id
    branch_name = "refs/heads/main"
    yml_path    = ".azure-pipelines/ci.yml"
  }
}
resource "azuredevops_build_definition" "release" {
  project_id              = data.azuredevops_project.main.id
  name                    = "${var.app_name}-CD"
  job_authorization_scope = "project"
  ci_trigger { use_yaml = true }
  repository {
    repo_type   = "TfsGit"
    repo_id     = data.azuredevops_git_repository.main.id
    branch_name = "refs/heads/main"
    yml_path    = "azure-pipelines.yml"
  }
  dynamic "variable" {
    for_each = {
      AZURE_SERVICE_CONNECTION      = azuredevops_serviceendpoint_azurerm.release.service_endpoint_name
      PLAN_SERVICE_CONNECTION       = azuredevops_serviceendpoint_azurerm.plan.service_endpoint_name
      PLAN_SERVICE_CONNECTION_ID    = azuredevops_serviceendpoint_azurerm.plan.id
      RELEASE_SERVICE_CONNECTION_ID = azuredevops_serviceendpoint_azurerm.release.id
      APP_NAME                      = var.app_name
      AZURE_LOCATION                = var.location
      VAULT_OPERATOR_ID             = var.secret_operator_object_id
      STATE_ACCOUNT                 = var.state_account_name
      STATE_CONTAINER               = var.state_container_name
      STATE_RESOURCE_GROUP          = var.state_resource_group_name
      STATE_KEY                     = var.state_key
      LOG_RETENTION_DAYS            = tostring(var.log_retention_days)
      RELEASE_ENVIRONMENT           = "${var.app_name}-production"
    }
    content {
      name           = variable.key
      value          = variable.value
      allow_override = false
    }
  }
}
resource "azuredevops_environment" "production" {
  project_id = data.azuredevops_project.main.id
  name       = "${var.app_name}-production"
}
resource "azuredevops_pipeline_authorization" "connection" {
  project_id  = data.azuredevops_project.main.id
  resource_id = azuredevops_serviceendpoint_azurerm.release.id
  type        = "endpoint"
  pipeline_id = azuredevops_build_definition.release.id
}
resource "azuredevops_pipeline_authorization" "environment" {
  project_id  = data.azuredevops_project.main.id
  resource_id = azuredevops_environment.production.id
  type        = "environment"
  pipeline_id = azuredevops_build_definition.release.id
}
resource "azuredevops_check_branch_control" "release" {
  project_id                       = data.azuredevops_project.main.id
  display_name                     = "Protected main only"
  target_resource_id               = azuredevops_serviceendpoint_azurerm.release.id
  target_resource_type             = "endpoint"
  allowed_branches                 = "refs/heads/main"
  verify_branch_protection         = true
  ignore_unknown_protection_status = false
}
resource "azuredevops_check_approval" "production" {
  project_id            = data.azuredevops_project.main.id
  target_resource_id    = azuredevops_serviceendpoint_azurerm.release.id
  target_resource_type  = "endpoint"
  approvers             = var.approver_ids
  requester_can_approve = false
  instructions          = "Review the saved Terraform plan and image tags from this run. Deployment restarts browser sessions."
  timeout               = 1440
}
resource "azuredevops_check_exclusive_lock" "production" {
  project_id           = data.azuredevops_project.main.id
  target_resource_id   = azuredevops_environment.production.id
  target_resource_type = "environment"
}
resource "azuredevops_branch_policy_build_validation" "main" {
  project_id = data.azuredevops_project.main.id
  enabled    = true
  blocking   = true
  settings {
    display_name                = "Scraper CI"
    build_definition_id         = azuredevops_build_definition.ci.id
    valid_duration              = 0
    queue_on_source_update_only = true
    scope {
      repository_id  = data.azuredevops_git_repository.main.id
      repository_ref = "refs/heads/main"
      match_type     = "Exact"
    }
  }
}
resource "azuredevops_branch_policy_min_reviewers" "main" {
  project_id = data.azuredevops_project.main.id
  enabled    = true
  blocking   = true
  settings {
    reviewer_count               = 1
    submitter_can_vote           = false
    last_pusher_cannot_approve   = true
    on_push_reset_approved_votes = true
    scope {
      repository_id  = data.azuredevops_git_repository.main.id
      repository_ref = "refs/heads/main"
      match_type     = "Exact"
    }
  }
}
output "ci_pipeline_id" { value = azuredevops_build_definition.ci.id }
output "cd_pipeline_id" { value = azuredevops_build_definition.release.id }
output "service_connection" { value = azuredevops_serviceendpoint_azurerm.release.service_endpoint_name }

resource "azurerm_user_assigned_identity" "plan" {
  name                = "id-${var.app_name}-plan"
  resource_group_name = data.azurerm_resource_group.identity.name
  location            = data.azurerm_resource_group.identity.location
}
resource "azuredevops_serviceendpoint_azurerm" "plan" {
  project_id                             = data.azuredevops_project.main.id
  service_endpoint_name                  = "${var.app_name}-plan"
  service_endpoint_authentication_scheme = "WorkloadIdentityFederation"
  credentials { serviceprincipalid = azurerm_user_assigned_identity.plan.client_id }
  azurerm_spn_tenantid      = data.azurerm_client_config.current.tenant_id
  azurerm_subscription_id   = var.subscription_id
  azurerm_subscription_name = var.subscription_name
}
resource "azurerm_federated_identity_credential" "plan" {
  name                      = "azure-devops-plan"
  user_assigned_identity_id = azurerm_user_assigned_identity.plan.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azuredevops_serviceendpoint_azurerm.plan.workload_identity_federation_issuer
  subject                   = azuredevops_serviceendpoint_azurerm.plan.workload_identity_federation_subject
}
resource "azurerm_role_assignment" "plan_read" {
  scope                = data.azurerm_resource_group.app.id
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.plan.principal_id
}
resource "azurerm_role_assignment" "plan_state" {
  scope                = azurerm_role_assignment.state.scope
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.plan.principal_id
}
resource "azuredevops_pipeline_authorization" "plan" {
  project_id  = data.azuredevops_project.main.id
  resource_id = azuredevops_serviceendpoint_azurerm.plan.id
  type        = "endpoint"
  pipeline_id = azuredevops_build_definition.release.id
}
resource "azuredevops_check_branch_control" "plan" {
  project_id                       = data.azuredevops_project.main.id
  display_name                     = "Protected main only"
  target_resource_id               = azuredevops_serviceendpoint_azurerm.plan.id
  target_resource_type             = "endpoint"
  allowed_branches                 = "refs/heads/main"
  verify_branch_protection         = true
  ignore_unknown_protection_status = false
}

# AzureRM refreshes these computed properties even during read-only planning.
resource "azurerm_role_definition" "refresh" {
  name              = "${var.app_name}-terraform-refresh"
  scope             = data.azurerm_resource_group.app.id
  assignable_scopes = [data.azurerm_resource_group.app.id]
  permissions {
    actions = ["Microsoft.App/containerApps/listSecrets/action", "Microsoft.OperationalInsights/workspaces/sharedKeys/action"]
  }
}
resource "azurerm_role_assignment" "refresh" {
  scope              = data.azurerm_resource_group.app.id
  role_definition_id = azurerm_role_definition.refresh.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.plan.principal_id
}
