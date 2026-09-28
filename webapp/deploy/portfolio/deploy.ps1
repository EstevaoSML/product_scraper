param(
    [ValidateSet('Validate','CheckSettings','Deploy','Smoke','Publish','CollectOne','CollectMonthly','SetSecret','EnableSchedule','DisableSchedule')]
    [string]$Action = 'Validate',
    [string]$Terraform = 'terraform',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$infra = Join-Path $PSScriptRoot 'terraform'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$scratch = Join-Path $repo '.ci-runtime/portfolio'
$varsFile = Join-Path $infra 'portfolio.auto.tfvars.json'
$outputsFile = Join-Path $scratch 'outputs.json'
$identityFile = Join-Path $infra '.deployment-identity'
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Exe (exit $LASTEXITCODE)" }
}
function Confirm-AzureSubscription([string]$SubscriptionId) {
    # account set/show can succeed using a stale local Azure CLI account cache.
    $json = & az account show --subscription $SubscriptionId --only-show-errors -o json
    if ($LASTEXITCODE -ne 0) { throw 'Azure login cannot select this subscription. Run az account list --refresh and log in to the correct tenant.' }
    $account = ($json -join [Environment]::NewLine) | ConvertFrom-Json
    if ($account.id -ne $SubscriptionId -or $account.state -ne 'Enabled' -or $account.environmentName -ne 'AzureCloud') {
        throw 'This deployment requires an Enabled subscription in AzureCloud. Check the subscription ID (not tenant ID), account and tenant.'
    }
    # Never print credential values. Fail rather than silently use another
    # Terraform identity while uploads and secret provisioning use Azure CLI.
    $overrides = @(Get-ChildItem Env:ARM_* | Where-Object {
        $_.Name -match '^ARM_(CLIENT_|TENANT_ID$|USE_OIDC$|USE_MSI$|OIDC_|MSI_)' -and $_.Value
    } | Select-Object -ExpandProperty Name)
    if ($overrides.Count -or ($env:ARM_USE_CLI -and $env:ARM_USE_CLI -ne 'true') -or
        ($env:ARM_ENVIRONMENT -and $env:ARM_ENVIRONMENT -ne 'public')) {
        throw 'Terraform authentication overrides are set in ARM_* environment variables. Use a clean PowerShell session with az login for this interactive deployment; do not print or share credential values.'
    }
    $url = 'https://management.azure.com/subscriptions/' + $SubscriptionId + '?api-version=2022-12-01'
    $json = & az rest --method get --url $url --only-show-errors -o json
    if ($LASTEXITCODE -ne 0) {
        throw 'Azure Resource Manager cannot access this subscription. Refresh az login in the correct tenant and verify an active Azure subscription in the portal. No packaging or apply was attempted.'
    }
    $subscription = ($json -join [Environment]::NewLine) | ConvertFrom-Json
    if ($subscription.subscriptionId -ne $SubscriptionId -or $subscription.state -ne 'Enabled') {
        throw 'Azure Resource Manager did not confirm the expected Enabled subscription. Deployment stopped.'
    }
}
function Write-Utf8File([string]$Path, [string]$Text) {
    # Windows PowerShell 5.1's Set-Content -Encoding utf8 adds a BOM.
    # Use an explicit encoding so Terraform receives the same bytes on 5.1/7.
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}
function Repair-VariablesEncoding {
    if (Test-Path -LiteralPath $varsFile) {
        $bytes = [IO.File]::ReadAllBytes($varsFile)
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {
            $text = [IO.File]::ReadAllText($varsFile)
            $null = $text | ConvertFrom-Json
            Write-Utf8File $varsFile $text
        }
    }
}
function Confirm-DeploymentIdentity($Settings, [string]$Identity) {
    # Only trust the default local state here. A migrated backend/workspace must
    # not be rebound using a leftover local state file.
    $workspaceFile = Join-Path $infra '.terraform/environment'
    $backendFile = Join-Path $infra '.terraform/terraform.tfstate'
    $stateFile = Join-Path $infra 'terraform.tfstate'
    $localDefault = !(Test-Path -LiteralPath $workspaceFile) -or (Get-Content -LiteralPath $workspaceFile -Raw).Trim() -eq 'default'
    if (Test-Path -LiteralPath $backendFile) {
        $backend = [IO.File]::ReadAllText($backendFile) | ConvertFrom-Json
        if ($backend.backend.type) { $localDefault = $false }
    }
    if ($localDefault -and (Test-Path -LiteralPath $stateFile)) {
        $state = [IO.File]::ReadAllText($stateFile) | ConvertFrom-Json
        if ($state.version -ne 4) { throw 'Unrecognized Terraform state format; identity cannot be verified safely.' }
        $managed = @($state.resources | Where-Object { $_.mode -eq 'managed' -and @($_.instances).Count -gt 0 })
        if ($managed.Count) {
            $groups = @($managed | Where-Object { $_.type -eq 'azurerm_resource_group' -and $_.name -eq 'portfolio' })
            if ($groups.Count -ne 1 -or @($groups[0].instances).Count -ne 1) {
                throw 'Existing resources found but project identity is ambiguous. Preserve state and inspect the deployment.'
            }
            $group = $groups[0].instances[0].attributes
            if ($group.id -notmatch '^/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/([^/]+)$') {
                throw 'Cannot verify resource-group identity from Terraform state.'
            }
            $recordedSubscription = $Matches[1]
            $recordedGroup = $Matches[2]
            if ($Settings.subscription_id -ne $recordedSubscription) {
                throw 'PRECO_SUBSCRIPTION_ID differs from the subscription in Terraform state. Restore the original subscription ID; do not clear the identity marker or state.'
            }
            if ($recordedGroup -ne ('rg-' + $Settings.name + '-portfolio')) {
                throw 'PRECO_NAME differs from the project in Terraform state. Keep the original project name; use PRECO_COMPUTE_LOCATION alone to change compute region.'
            }
            # Matching managed state is authoritative even if the local marker
            # is stale. The marker is refreshed only after Azure preflight passes.
            return
        }
    }
    if ((Test-Path -LiteralPath $identityFile) -and (Get-Content -LiteralPath $identityFile -Raw).Trim() -ne $Identity) {
        throw 'These environment settings target another deployment: PRECO_SUBSCRIPTION_ID or PRECO_NAME changed. State could not confirm a safe rebind; preserve the marker and state.'
    }
}
function Save-Variables($Values) {
    # Persist only rollout controls, never operator-provided settings.
    $controls = @{}
    foreach ($key in @('deploy_job','enable_monthly_schedule','package_sha256','budget_start_date')) {
        $controls[$key] = $Values.$key
    }
    Write-Utf8File $varsFile ($controls | ConvertTo-Json)
}
function Save-Outputs {
    $json = & $Terraform "-chdir=$infra" output -json
    if ($LASTEXITCODE -ne 0) { throw 'Terraform outputs unavailable' }
    Write-Utf8File $outputsFile ($json -join [Environment]::NewLine)
}
function Apply-Plan {
    $plan = Join-Path $scratch 'portfolio.tfplan'
    Invoke-Checked $Terraform @("-chdir=$infra", 'plan', "-out=$plan")
    # This script is explicitly invoked for deployment. The saved plan is the
    # reviewed configuration; no hidden terraform destroy or resource migration.
    Invoke-Checked $Terraform @("-chdir=$infra", 'apply', $plan)
    Save-Outputs
}
$savedEnvironment = @{}
try {
    if ($Action -ne 'Validate') {
        $settings = @{
            subscription_id = $env:PRECO_SUBSCRIPTION_ID
            name = $env:PRECO_NAME
            location = $(if ($env:PRECO_LOCATION) { $env:PRECO_LOCATION } else { 'eastus' })
            compute_location = $(if ($env:PRECO_COMPUTE_LOCATION) { $env:PRECO_COMPUTE_LOCATION } else { '' })
            alert_email = $env:PRECO_ALERT_EMAIL
            suggestion_email = $(if ($env:PRECO_SUGGESTION_EMAIL) { $env:PRECO_SUGGESTION_EMAIL } else { '' })
        }
        foreach ($key in @($settings.Keys)) { $settings[$key] = ([string]$settings[$key]).Trim() }
        if ($settings.subscription_id -notmatch '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$') {
            throw 'Set $env:PRECO_SUBSCRIPTION_ID to your real Azure subscription UUID (no placeholder).'
        }
        if ($settings.name -notmatch '^[a-z][a-z0-9]{5,15}$' -or $settings.name -cmatch '[A-Z]') {
            throw 'Set $env:PRECO_NAME to a unique 6-16 character lowercase name, starting with a letter.'
        }
        if ($Action -ne 'CheckSettings' -and $settings.alert_email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
            throw 'Set $env:PRECO_ALERT_EMAIL to the address that should receive Azure cost alerts.'
        }
        if ($Action -ne 'CheckSettings' -and $settings.suggestion_email -and $settings.suggestion_email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
            throw 'PRECO_SUGGESTION_EMAIL must be empty or a valid email address.'
        }
        # TF_VAR_* values are inherited by Terraform only for this script run.
        foreach ($key in $settings.Keys) {
            $envName = 'TF_VAR_' + $key
            $savedEnvironment[$envName] = [Environment]::GetEnvironmentVariable($envName, 'Process')
            [Environment]::SetEnvironmentVariable($envName, $settings[$key], 'Process')
        }
        $hasher = [Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes($settings.subscription_id.ToLowerInvariant() + '/' + $settings.name)
            $identity = ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
        } finally { $hasher.Dispose() }
        Confirm-DeploymentIdentity $settings $identity
        if ($Action -eq 'CheckSettings') {
            Write-Host 'Subscription and project settings passed the local identity check. No Azure calls or file changes made.'
            return
        }
        # Migrate the old generated file without letting its higher-precedence
        # values silently override the new environment settings.
        if (Test-Path -LiteralPath $varsFile) {
            $previous = Get-Content -LiteralPath $varsFile -Raw | ConvertFrom-Json
            if (($previous.name -and $previous.name -ne $settings.name) -or
                ($previous.subscription_id -and $previous.subscription_id -ne $settings.subscription_id)) {
                throw 'Environment settings differ from the existing deployment. Keep the original subscription/name or use separate state.'
            }
            Write-Utf8File $identityFile $identity
            Save-Variables $previous
        }
    }
    New-Item -ItemType Directory -Force -Path $scratch | Out-Null
    Repair-VariablesEncoding
    Invoke-Checked $Terraform @("-chdir=$infra", 'init', '-input=false')
    Invoke-Checked $Terraform @("-chdir=$infra", 'validate')
    if ($Action -eq 'Validate') { return }
    Invoke-Checked 'az' @('account', 'set', '--subscription', $settings.subscription_id)
    Confirm-AzureSubscription $settings.subscription_id
    Write-Utf8File $identityFile $identity
    if ($Action -eq 'Deploy') {
        # Build before creating billable resources; dependency download is local,
        # while all scraping, model research and scheduled work execute on Azure.
        Invoke-Checked $Python @((Join-Path $PSScriptRoot 'package.py'), '--output', $scratch)
        $values = @{
            deploy_job = $false
            enable_monthly_schedule = $false; package_sha256 = ''
            budget_start_date = (Get-Date).ToUniversalTime().ToString('yyyy-MM-01T00:00:00Z')
        }
        if (Test-Path -LiteralPath $varsFile) {
            $previous = Get-Content -LiteralPath $varsFile -Raw | ConvertFrom-Json
            $values.deploy_job = $previous.deploy_job
            $values.package_sha256 = $previous.package_sha256
            $values.budget_start_date = $previous.budget_start_date
        }
        Save-Variables $values
        Apply-Plan
        Invoke-Checked $Python @((Join-Path $PSScriptRoot 'operations.py'), 'upload', '--outputs', $outputsFile, '--package', (Join-Path $scratch 'package.json'))
        $package = Get-Content -LiteralPath (Join-Path $scratch 'package.json') -Raw | ConvertFrom-Json
        $values.package_sha256 = $package.sha256
        $values.deploy_job = $true
        Save-Variables $values
        Apply-Plan
        Write-Host 'Deployed with scheduling disabled. Run -Action Smoke next; no OpenAI key is needed.'
        return
    }
    Save-Outputs
    if ($Action -eq 'SetSecret') {
        Invoke-Checked $Python @((Join-Path $PSScriptRoot 'operations.py'), 'set-secret', '--outputs', $outputsFile)
    } elseif ($Action -in @('EnableSchedule','DisableSchedule')) {
        if ($Action -eq 'EnableSchedule') {
            Invoke-Checked $Python @((Join-Path $PSScriptRoot 'operations.py'), 'check-ready', '--outputs', $outputsFile)
        }
        $values = Get-Content -LiteralPath $varsFile -Raw | ConvertFrom-Json
        $values.enable_monthly_schedule = $Action -eq 'EnableSchedule'
        Save-Variables $values
        Apply-Plan
    } else {
        $mode = switch ($Action) { 'Smoke' { 'smoke' } 'Publish' { 'publish' } default { 'collect' } }
        $limit = if ($Action -eq 'CollectOne') { '1' } else { '40' }
        if ($mode -eq 'collect') {
            Invoke-Checked $Python @((Join-Path $PSScriptRoot 'operations.py'), 'check-ready', '--outputs', $outputsFile)
        }
        Invoke-Checked $Python @((Join-Path $PSScriptRoot 'operations.py'), 'start', '--outputs', $outputsFile, '--mode', $mode, '--limit', $limit)
    }
} finally {
    foreach ($envName in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($envName, $savedEnvironment[$envName], 'Process')
    }
}
