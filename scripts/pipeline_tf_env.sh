#!/usr/bin/env bash
# Source inside an AzureCLI@2 WIF task. Never enable shell tracing here.
set -euo pipefail
export ARM_USE_OIDC=true ARM_USE_AZUREAD=true ARM_USE_CLI=false
export ARM_CLIENT_ID="${servicePrincipalId:?AzureCLI WIF identity missing}"
export ARM_TENANT_ID="${tenantId:?AzureCLI tenant missing}"
export ARM_SUBSCRIPTION_ID
ARM_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
# Use the job's renewable OIDC endpoint, not a short-lived token saved in a plan.
export ARM_OIDC_REQUEST_TOKEN="${SYSTEM_ACCESSTOKEN:?Job OAuth token missing}"
export ARM_OIDC_REQUEST_URL="${SYSTEM_OIDCREQUESTURI:?Job OIDC URI missing}"
export ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID="${PIPELINE_SERVICE_CONNECTION_ID:?Service connection missing}"
export ARM_OIDC_AZURE_SERVICE_CONNECTION_ID="$ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID"
unset ARM_OIDC_TOKEN ARM_CLIENT_SECRET
export TF_IN_AUTOMATION=true TF_INPUT=false
export TF_VAR_subscription_id="$ARM_SUBSCRIPTION_ID"
export TF_VAR_name="${APP_NAME:?}"
export TF_VAR_location="${AZURE_LOCATION:?}"
export TF_VAR_secret_operator_object_id="${VAULT_OPERATOR_ID:?}"
export TF_VAR_log_retention_days="${LOG_RETENTION_DAYS:?}"
export TF_VAR_deploy_workloads=true
export TF_VAR_api_image_tag="${RELEASE_TAG:?}"
export TF_VAR_browser_image_tag="$TF_VAR_api_image_tag"
terraform -chdir=deploy/terraform init -input=false -reconfigure \
  -backend-config="storage_account_name=${STATE_ACCOUNT:?}" \
  -backend-config="container_name=${STATE_CONTAINER:?}" \
  -backend-config="resource_group_name=${STATE_RESOURCE_GROUP:?}" \
  -backend-config="key=${STATE_KEY:?}" \
  -backend-config=use_azuread_auth=true -backend-config=use_oidc=true
