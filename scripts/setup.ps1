$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$secretDir = Join-Path $repo 'secrets'
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
$keyFile = Join-Path $secretDir 'api_key.txt'
if (-not (Test-Path -LiteralPath $keyFile)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($keyFile, [Convert]::ToBase64String($bytes))
}
$proxyFile = Join-Path $secretDir 'upstream_proxy.txt'
if (-not (Test-Path -LiteralPath $proxyFile)) { [IO.File]::WriteAllText($proxyFile, '') }
Write-Output 'Secret files are ready. Run docker compose up --build -d, then scripts/smoke.ps1.'
