param(
    [ValidateSet('Validate','CheckSettings','Deploy','Smoke','Publish','CollectOne','CollectMonthly','SetSecret','EnableSchedule','DisableSchedule')]
    [string]$Action = 'Validate',
    [string]$Terraform = 'terraform',
    [string]$Python = 'python',
    [string]$Month = ''
)
$ErrorActionPreference = 'Stop'
if ($Month -and ($Action -notin @('CollectOne','CollectMonthly') -or $Month -cne (Get-Date).ToUniversalTime().ToString('yyyy-MM'))) {
    throw 'Month must be the current UTC month (YYYY-MM) and used only with CollectOne or CollectMonthly. Live prices cannot be backdated.'
}
$infra = Join-Path $PSScriptRoot 'terraform'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$scratch = Join-Path $repo '.ci-runtime/portfolio'
$varsFile = Join-Path $infra 'portfolio.auto.tfvars.json'
$outputsFile = Join-Path $scratch 'outputs.json'
$settingsFile = Join-Path $repo 'portfolio-deployment.local.json'
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
function Read-DeploymentSettings {
    if (!(Test-Path -LiteralPath $settingsFile)) {
        throw 'Create portfolio-deployment.local.json in the repository root from portfolio-deployment.example.json and fill in your settings.'
    }
    try { $config = [IO.File]::ReadAllText($settingsFile) | ConvertFrom-Json }
    catch { throw 'portfolio-deployment.local.json must contain a valid JSON object.' }
    $keys = @('subscription_id','name','location','compute_location','alert_email','suggestion_email')
    if ($config -isnot [PSCustomObject]) { throw 'portfolio-deployment.local.json must contain a JSON object.' }
    foreach ($property in $config.PSObject.Properties) {
        if ($property.Name -cnotin $keys) { throw 'Unknown configuration field. Use only the fields in portfolio-deployment.example.json; keep secrets in Key Vault.' }
    }
    foreach ($key in $keys) {
        if ($config.$key -isnot [string] -or $config.$key -cne $config.$key.Trim()) {
            throw "Configuration field '$key' must be a string without surrounding whitespace."
        }
    }
    if ($config.subscription_id -notmatch '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$') {
        throw 'Set subscription_id in portfolio-deployment.local.json to your real Azure subscription UUID.'
    }
    if ($config.name -cnotmatch '^[a-z][a-z0-9]{5,15}$') {
        throw 'Set name to 6-16 lowercase letters/digits, starting with a letter.'
    }
    if ($config.location -cnotmatch '^[a-z][a-z0-9]+$' -or ($config.compute_location -and $config.compute_location -cnotmatch '^[a-z][a-z0-9]+$')) {
        throw 'Use Azure region codes for location and compute_location; compute_location can be empty.'
    }
    if ($config.alert_email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$' -or ($config.suggestion_email -and $config.suggestion_email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$')) {
        throw 'Set a valid alert_email; suggestion_email can be empty or a valid email address.'
    }
    return $config
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
    Invoke-Checked $Terraform @("-chdir=$infra", 'plan', "-var-file=$settingsFile", "-out=$plan")
    # This script is explicitly invoked for deployment. The saved plan is the
    # reviewed configuration; no hidden terraform destroy or resource migration.
    Invoke-Checked $Terraform @("-chdir=$infra", 'apply', $plan)
    Save-Outputs
}
if ($Action -ne 'Validate') {
    $settings = Read-DeploymentSettings
    if ($Action -eq 'CheckSettings') {
        Write-Host 'Configuration file passed validation. No Azure calls or file changes made.'
        return
    }
    # Fail locally before packaging or any Terraform/Azure work.
    Invoke-Checked $Python @((Join-Path $PSScriptRoot 'operations.py'), 'check-dependencies')
    # Remove legacy input copies; the explicit JSON -var-file is authoritative.
    if (Test-Path -LiteralPath $varsFile) {
        $previous = [IO.File]::ReadAllText($varsFile) | ConvertFrom-Json
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
    $limit = if ($Action -eq 'CollectOne') { '1' } else { '150' }
    if ($mode -eq 'collect') {
        Invoke-Checked $Python @((Join-Path $PSScriptRoot 'operations.py'), 'check-ready', '--outputs', $outputsFile)
    }
    $startArgs = @((Join-Path $PSScriptRoot 'operations.py'), 'start', '--outputs', $outputsFile, '--mode', $mode, '--limit', $limit)
    if ($Month) { $startArgs += @('--month', $Month) }
    Invoke-Checked $Python $startArgs
}
