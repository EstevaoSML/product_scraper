param(
    [ValidateSet('Validate','Deploy','Smoke','Publish','CollectOne','CollectMonthly','SetSecret','EnableSchedule','DisableSchedule')]
    [string]$Action = 'Validate',
    [string]$Config = "$PSScriptRoot/config.local.json",
    [string]$Terraform = 'terraform',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$infra = Join-Path $PSScriptRoot 'terraform'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$scratch = Join-Path $repo '.ci-runtime/portfolio'
$varsFile = Join-Path $infra 'portfolio.auto.tfvars.json'
$outputsFile = Join-Path $scratch 'outputs.json'
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Exe (exit $LASTEXITCODE)" }
}
function Save-Variables($Values) {
    $Values | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $varsFile -Encoding utf8
}
function Save-Outputs {
    $json = & $Terraform "-chdir=$infra" output -json
    if ($LASTEXITCODE -ne 0) { throw 'Terraform outputs unavailable' }
    $json | Set-Content -LiteralPath $outputsFile -Encoding utf8
}
function Apply-Plan {
    $plan = Join-Path $scratch 'portfolio.tfplan'
    Invoke-Checked $Terraform @("-chdir=$infra", 'plan', "-out=$plan")
    # This script is explicitly invoked for deployment. The saved plan is the
    # reviewed configuration; no hidden terraform destroy or resource migration.
    Invoke-Checked $Terraform @("-chdir=$infra", 'apply', $plan)
    Save-Outputs
}
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
Invoke-Checked $Terraform @("-chdir=$infra", 'init', '-input=false')
Invoke-Checked $Terraform @("-chdir=$infra", 'validate')
if ($Action -eq 'Validate') { return }
if (!(Test-Path -LiteralPath $Config)) { throw 'Copy config.example.json to config.local.json and fill in your subscription, unique name and email.' }
$settings = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
Invoke-Checked 'az' @('account', 'set', '--subscription', $settings.subscription_id)
if ($Action -eq 'Deploy') {
    # Build before creating billable resources; dependency download is local,
    # while all scraping, model research and scheduled work execute on Azure.
    Invoke-Checked $Python @((Join-Path $PSScriptRoot 'package.py'), '--output', $scratch)
    $values = @{
        subscription_id = $settings.subscription_id; name = $settings.name
        location = $settings.location; alert_email = $settings.alert_email
        suggestion_email = $settings.suggestion_email; deploy_job = $false
        enable_monthly_schedule = $false; package_sha256 = ''
        budget_start_date = (Get-Date).ToUniversalTime().ToString('yyyy-MM-01T00:00:00Z')
    }
    if (Test-Path -LiteralPath $varsFile) {
        $previous = Get-Content -LiteralPath $varsFile -Raw | ConvertFrom-Json
        if ($previous.name -ne $values.name -or $previous.subscription_id -ne $values.subscription_id) {
            throw 'Use a separate Terraform directory/state for another project. Existing identity cannot be overwritten.'
        }
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
