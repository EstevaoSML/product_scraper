param([string]$Url = 'https://example.com', [string]$BaseUrl = 'http://localhost:8000')
$ErrorActionPreference = 'Stop'
$executionTimestamp = [DateTime]::UtcNow.ToString("yyyyMMdd_HHmmss_fff'Z'")
$executionId = [Guid]::NewGuid().ToString('N')
$repo = Split-Path -Parent $PSScriptRoot
try {
$key = (Get-Content -Raw -LiteralPath (Join-Path $repo 'secrets/api_key.txt')).Trim()
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod "$BaseUrl/readyz" -TimeoutSec 5 | Out-Null; $ready = $true; break }
    catch { Start-Sleep -Seconds 3 }
}
if (-not $ready) { throw 'Browser did not become ready. Check docker compose logs.' }
$body = @{url=$Url; wait_seconds=5; timeout_seconds=30} | ConvertTo-Json
$result = Invoke-RestMethod "$BaseUrl/scrape" -Method Post -Headers @{'X-API-Key'=$key} -ContentType 'application/json' -Body $body -TimeoutSec 180
if (-not $result.html -or -not $result.browser_version) { throw 'Response lacks HTML or browser version' }
$outputDir = Join-Path $repo 'outputs'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$outputFile = Join-Path $outputDir "scrape_${executionTimestamp}_${executionId}.json"
# CreateNew guarantees an existing result is never overwritten, even on collision.
$jsonBytes = [System.Text.Encoding]::UTF8.GetBytes(($result | ConvertTo-Json -Depth 5))
$outputStream = [System.IO.File]::Open($outputFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
try { $outputStream.Write($jsonBytes, 0, $jsonBytes.Length) }
finally { $outputStream.Dispose() }
$result | Select-Object title,final_url,html_bytes,browser_version,elapsed_ms
Write-Output "Saved $outputFile"
} catch {
    # Never persist the raw PowerShell error: it can contain URLs, keys or HTML.
    $code = 'client_error'
    $message = 'Request failed. Check API availability, readiness, key file and output permissions.'
    $requestId = $null
    try {
        $apiError = $_.ErrorDetails.Message | ConvertFrom-Json
        $messages = @{
            host_not_allowed = 'Add the exact hostname to ALLOWED_HOSTS in .env and recreate the API container.'
            dns_resolution_failed = 'Destination DNS lookup failed. Check DNS and retry.'
            non_public_destination = 'Destination resolves to a blocked address.'
            invalid_url = 'Use a plain HTTPS URL on port 443, without Markdown formatting.'
            url_credentials_forbidden = 'Remove credentials from the URL.'
            unauthorized = 'API key rejected.'
            invalid_request = 'Request fields are invalid.'
            request_too_large = 'Request body is too large.'
            response_too_large = 'Rendered page exceeds the response size limit.'
            browser_busy = 'Browser busy; wait before retrying.'
            browser_error = 'Browser operation failed.'
            browser_unavailable = 'Browser unavailable.'
            browser_timeout = 'Browser operation timed out.'
            internal_error = 'Unexpected API error.'
        }
        if ($messages.ContainsKey([string]$apiError.code)) {
            $code = [string]$apiError.code
            $message = $messages[$code]
        } elseif ([string]$apiError.code -match '^redirect_(host_not_allowed|dns_resolution_failed|non_public_destination|invalid_url|url_credentials_forbidden)$') {
            $code = [string]$apiError.code
            $message = 'Final redirect was rejected by the URL policy.'
        }
        if ([string]$apiError.request_id -match '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') {
            $requestId = [string]$apiError.request_id
        }
    } catch { } # Network errors and non-JSON responses use the safe fallback.
    $logDir = Join-Path $repo 'logs'
    $logFile = Join-Path $logDir 'client-errors.jsonl'
    try {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        # Bounded storage: active file plus five backups. Run one smoke client at a time.
        if ((Test-Path -LiteralPath $logFile) -and (Get-Item -LiteralPath $logFile).Length -ge 5000000) {
            for ($n = 4; $n -ge 1; $n--) {
                if (Test-Path -LiteralPath "$logFile.$n") {
                    Move-Item -LiteralPath "$logFile.$n" -Destination "$logFile.$($n + 1)" -Force
                }
            }
            Move-Item -LiteralPath $logFile -Destination "$logFile.1" -Force
        }
        @{timestamp=[DateTime]::UtcNow.ToString('o'); code=$code; message=$message; request_id=$requestId} |
            ConvertTo-Json -Compress | Add-Content -Encoding utf8 -LiteralPath $logFile
    } catch {
        throw "$code`: $message Request ID: $requestId. Error log could not be written; check logs directory permissions."
    }
    throw "$code`: $message Request ID: $requestId. Saved error to $logFile"
}
