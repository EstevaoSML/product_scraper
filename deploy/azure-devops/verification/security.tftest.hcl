mock_provider "azurerm" {}
mock_provider "azuredevops" {}
variables {
  subscription_id              = "00000000-0000-0000-0000-000000000001"
  subscription_name            = "CI subscription"
  project_name                 = "CI project"
  repository_name              = "CI repo"
  app_name                     = "scraperci123"
  identity_resource_group_name = "rg-identities"
  state_account_name           = "scrapercistate"
  state_resource_group_name    = "rg-state"
  state_key                    = "scraper.tfstate"
  secret_operator_object_id    = "00000000-0000-0000-0000-000000000002"
  approver_ids                 = ["00000000-0000-0000-0000-000000000003"]
}
override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000004"
  }
}
override_data {
  target = data.azurerm_resource_group.app
  values = {
    id       = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-scraperci123"
    name     = "rg-scraperci123"
    location = "brazilsouth"
  }
}
override_data {
  target = data.azurerm_resource_group.identity
  values = {
    id       = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-identities"
    name     = "rg-identities"
    location = "brazilsouth"
  }
}
override_data {
  target = data.azurerm_user_assigned_identity.api
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-scraperci123/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-api"
    principal_id = "00000000-0000-0000-0000-000000000005"
  }
}
override_data {
  target = data.azurerm_user_assigned_identity.browser
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-scraperci123/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-browser"
    principal_id = "00000000-0000-0000-0000-000000000006"
  }
}
override_data {
  target = data.azuredevops_project.main
  values = {
    id = "00000000-0000-0000-0000-000000000007"
  }
}
override_data {
  target = data.azuredevops_git_repository.main
  values = {
    id             = "00000000-0000-0000-0000-000000000008"
    default_branch = "refs/heads/main"
  }
}
run "release_security" {
  command = plan
  assert {
    condition     = azuredevops_serviceendpoint_azurerm.plan.service_endpoint_authentication_scheme == "WorkloadIdentityFederation" && azuredevops_serviceendpoint_azurerm.release.service_endpoint_authentication_scheme == "WorkloadIdentityFederation"
    error_message = "Connections must use federation, never client passwords."
  }
  assert {
    condition     = azuredevops_check_branch_control.release.allowed_branches == "refs/heads/main" && azuredevops_check_branch_control.release.verify_branch_protection && !azuredevops_check_branch_control.release.ignore_unknown_protection_status
    error_message = "Unprotected and PR branches must not receive deployment credentials."
  }
  assert {
    condition     = !azuredevops_check_approval.production.requester_can_approve && azuredevops_check_approval.production.target_resource_type == "endpoint"
    error_message = "Deployment identity must be approval-gated outside pipeline YAML."
  }
  assert {
    condition     = azurerm_role_assignment.plan_state.role_definition_name == "Storage Blob Data Reader" && azurerm_role_assignment.app_rbac.condition_version == "2.0"
    error_message = "Plan must not write state and release RBAC delegation must be constrained."
  }
  assert {
    condition     = azuredevops_branch_policy_build_validation.main.blocking && azuredevops_branch_policy_min_reviewers.main.settings[0].reviewer_count == 1
    error_message = "Changes must pass CI and independent review before main."
  }
}
