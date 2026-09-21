param([Parameter(Mandatory=$true)][string]$VaultName)
$ErrorActionPreference = 'Stop'
# Requires Connect-AzAccount and network access to this vault. Never prints values.
Import-Module Az.KeyVault -ErrorAction Stop
$existing = @(Get-AzKeyVaultSecret -VaultName $VaultName | ForEach-Object { $_.Name })
foreach ($name in @('mcp-api-key', 'scraper-api-key')) {
    if ($existing -contains $name) {
        Write-Host "Keeping existing secret: $name"
        continue
    }
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        $secure = ConvertTo-SecureString ([Convert]::ToBase64String($bytes)) -AsPlainText -Force
        try {
            Set-AzKeyVaultSecret -VaultName $VaultName -Name $name -SecretValue $secure | Out-Null
        } finally { $secure.Dispose() }
        Write-Host "Created secret: $name"
    } finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
        $rng.Dispose()
    }
}
