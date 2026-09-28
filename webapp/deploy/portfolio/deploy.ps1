param(
    [ValidateSet('Validate','Deploy','Smoke','Publish','CollectOne','CollectMonthly','SetSecret','EnableSchedule','DisableSchedule')]
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
            alert_email = $env:PRECO_ALERT_EMAIL
            suggestion_email = $(if ($env:PRECO_SUGGESTION_EMAIL) { $env:PRECO_SUGGESTION_EMAIL } else { '' })
        }
        if ($settings.subscription_id -notmatch '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$') {
            throw 'Set $env:PRECO_SUBSCRIPTION_ID to your real Azure subscription UUID (no placeholder).'
        }
        if ($settings.name -notmatch '^[a-z][a-z0-9]{5,15}$' -or $settings.name -cmatch '[A-Z]') {
            throw 'Set $env:PRECO_NAME to a unique 6-16 character lowercase name, starting with a letter.'
        }
        if ($settings.alert_email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
            throw 'Set $env:PRECO_ALERT_EMAIL to the address that should receive Azure cost alerts.'
        }
        if ($settings.suggestion_email -and $settings.suggestion_email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
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
        if ((Test-Path -LiteralPath $identityFile) -and (Get-Content -LiteralPath $identityFile -Raw).Trim() -ne $identity) {
            throw 'These environment settings target another deployment. Use a separate Terraform directory/state.'
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
