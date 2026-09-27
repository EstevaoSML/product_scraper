[CmdletBinding()]
param(
    [string]$Config = "$PSScriptRoot/config.json",
    [switch]$Apply,
    [string]$ImageTag = (Get-Date -Format 'yyyyMMddHHmmss')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$site = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $PSScriptRoot 'terraform'
$bootstrap = Join-Path $PSScriptRoot 'bootstrap'
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE). Deployment stopped." }
}
Get-Command terraform -ErrorAction Stop | Out-Null
Invoke-Checked 'terraform' @("-chdir=$tf", 'fmt', '-check', '-recursive')
if (-not $Apply) {
    foreach ($directory in @($bootstrap, $tf)) {
        Invoke-Checked 'terraform' @("-chdir=$directory", 'init', '-backend=false', '-input=false')
        Invoke-Checked 'terraform' @("-chdir=$directory", 'validate')
    }
    Invoke-Checked 'terraform' @("-chdir=$tf", 'test','-test-directory=verification')
    Write-Host 'Local validation complete. Use -Apply to provision Azure, build images and publish the existing catalog.'
    return
}
foreach ($program in @('az', 'python')) { Get-Command $program -ErrorAction Stop | Out-Null }
if ($ImageTag -notmatch '^[a-zA-Z0-9_.-]{1,100}$') { throw 'Invalid image tag.' }
$settings = Get-Content -Raw -LiteralPath $Config | ConvertFrom-Json
if ($settings.name -notmatch '^[a-z][a-z0-9]{5,15}$') { throw 'Use a globally unique name with 6-16 lowercase letters/digits.' }
$parsedIp = $null
if (-not [System.Net.IPAddress]::TryParse($settings.operator_ipv4, [ref]$parsedIp) -or $parsedIp.AddressFamily -ne 'InterNetwork') { throw 'Configure your public IPv4 address.' }
Invoke-Checked 'az' @('account', 'set', '--subscription', $settings.subscription_id)
# This explicit -Apply command is the operator's authorization to create paid resources.
foreach ($provider in @('Microsoft.App','Microsoft.ContainerRegistry','Microsoft.KeyVault','Microsoft.Storage','Microsoft.Network','Microsoft.OperationalInsights','Microsoft.ManagedIdentity','Microsoft.Insights')) {
    Invoke-Checked 'az' @('provider','register','--namespace',$provider,'--wait','--only-show-errors')
}
Invoke-Checked 'az' @('extension','add','--name','containerapp','--upgrade','--only-show-errors')
Invoke-Checked 'python' @('-m','pip','install','-r', (Join-Path $site 'requirements-deploy.txt'))
$bootVars = @{
    subscription_id = $settings.subscription_id; name = $settings.name; location = $settings.location
    operator_object_id = $settings.secret_operator_object_id; operator_ipv4 = $settings.operator_ipv4
}
$bootVars | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $bootstrap 'deploy.auto.tfvars.json')
Invoke-Checked 'terraform' @("-chdir=$bootstrap",'init','-input=false')
Invoke-Checked 'terraform' @("-chdir=$bootstrap",'apply','-auto-approve','-input=false')
$stateAccount = "$($settings.name)tfstate"
$backend = Join-Path $PSScriptRoot 'backend.generated.hcl'
@"
resource_group_name = "rg-$($settings.name)-state"
storage_account_name = "$stateAccount"
container_name = "tfstate"
key = "preco-claro.tfstate"
use_azuread_auth = true
subscription_id = "$($settings.subscription_id)"
"@ | Set-Content -Encoding utf8 $backend
# RBAC propagation can take several minutes; retries are bounded.
for ($attempt=0; $attempt -lt 20; $attempt++) {
    & terraform "-chdir=$tf" init -reconfigure "-backend-config=$backend" -input=false
    if ($LASTEXITCODE -eq 0) { break }
    if ($attempt -eq 19) { throw 'State backend not accessible. Check RBAC and operator IPv4.' }
    Start-Sleep -Seconds 15
}
$existing = & terraform "-chdir=$tf" output -json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing deployment.' }
$hasWeb = $null -ne $existing.PSObject.Properties['web_url']
$hasFoundation = $null -ne $existing.PSObject.Properties['registry']
$vars = @{
    subscription_id=$settings.subscription_id; name=$settings.name; location=$settings.location
    secret_operator_object_id=$settings.secret_operator_object_id
    operator_ipv4_cidrs=@("$($settings.operator_ipv4)/32")
    suggestion_email=$settings.suggestion_email; collection_cron=$settings.collection_cron
    deploy_workloads=$false
}
$varsFile = Join-Path $tf 'deploy.auto.tfvars.json'
if (-not $hasFoundation) {
    $vars | ConvertTo-Json | Set-Content -Encoding utf8 $varsFile
    Invoke-Checked 'terraform' @("-chdir=$tf",'apply','-auto-approve','-input=false')
}
$registry = "$($settings.name)acr"
$vault = "kv-$($settings.name)"
# Open only the operator's /32 while initializing/rotating secrets; close in finally.
try {
    Invoke-Checked 'az' @('keyvault','network-rule','add','--name',$vault,'--ip-address',"$($settings.operator_ipv4)/32",'--output','none')
    Invoke-Checked 'az' @('keyvault','update','--name',$vault,'--public-network-access','Enabled','--output','none')
    $secret = Read-Host 'OpenAI API key for first deployment (Enter if already in Key Vault)' -AsSecureString
    $env:DEPLOY_OPENAI_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        & python (Join-Path $PSScriptRoot 'seed_secrets.py') $vault
        if ($LASTEXITCODE -eq 0) { break }
        if ($attempt -eq 19) { throw 'Key Vault initialization failed.' }
        Start-Sleep -Seconds 15
    }
} finally {
    Remove-Item Env:DEPLOY_OPENAI_KEY -ErrorAction SilentlyContinue
    Invoke-Checked 'az' @('keyvault','update','--name',$vault,'--public-network-access','Disabled','--output','none')
}
# ACR builds run Linux in Azure: no local Docker engine is required.
Invoke-Checked 'az' @('acr','build','--registry',$registry,'--image',"html-scraper:$ImageTag",'--file','Dockerfile',(Join-Path $site 'vendor/scraper'))
Invoke-Checked 'az' @('acr','build','--registry',$registry,'--image',"html-scraper-browser:$ImageTag",'--file','Dockerfile.browser',(Join-Path $site 'vendor/scraper'))
Invoke-Checked 'az' @('acr','build','--registry',$registry,'--image',"preco-claro:$ImageTag",'--file','Dockerfile',$site)
Invoke-Checked 'az' @('acr','build','--registry',$registry,'--image',"preco-claro-worker:$ImageTag",'--file','Dockerfile.worker',$site)
$vars.deploy_workloads = $true
$vars.deploy_web = $hasWeb
$vars.operator_ipv4_cidrs = @()
$vars.web_image_tag = $ImageTag; $vars.worker_image_tag = $ImageTag
$vars.api_image_tag = $ImageTag; $vars.browser_image_tag = $ImageTag
$vars | ConvertTo-Json | Set-Content -Encoding utf8 $varsFile
Invoke-Checked 'terraform' @("-chdir=$tf",'plan','-out=deploy.tfplan','-input=false')
Invoke-Checked 'terraform' @("-chdir=$tf",'apply','-input=false','deploy.tfplan')
$group = "rg-$($settings.name)"
$execution = & az containerapp job start --name "$($settings.name)-seed" --resource-group $group --query name -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Unable to start seed job.' }
for ($attempt=0; $attempt -lt 120; $attempt++) {
    $status = & az containerapp job execution show --name "$($settings.name)-seed" --resource-group $group --job-execution-name $execution --query properties.status -o tsv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect seed job.' }
    if ($status -eq 'Succeeded') { break }
    if ($status -in @('Failed','Stopped','Degraded') -or $attempt -eq 119) { throw "Seed job did not complete: $status" }
    Start-Sleep -Seconds 20
}
$vars.deploy_web = $true
$vars | ConvertTo-Json | Set-Content -Encoding utf8 $varsFile
Invoke-Checked 'terraform' @("-chdir=$tf",'apply','-auto-approve','-input=false')
$url = & terraform "-chdir=$tf" output -raw web_url
if ($LASTEXITCODE -ne 0) { throw 'Web URL unavailable.' }
$siteReady = $false
for ($attempt=0; $attempt -lt 12; $attempt++) {
    try {
        $ready = Invoke-RestMethod "$url/readyz" -TimeoutSec 30
        if ($ready.status -eq 'ready') { $siteReady = $true; break }
    } catch { if ($attempt -eq 11) { throw } }
    Start-Sleep -Seconds 10
}
if (-not $siteReady) { throw 'Website did not report ready. Deployment is not validated.' }
Write-Host "Deployment complete: $url"
Write-Host 'Existing 5-product catalog published. Collection job is manual unless collection_cron was configured.'
