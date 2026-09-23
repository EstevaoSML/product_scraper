# Using the scraper locally with Docker

This guide explains how to run the scraper on your own machine, send a website URL, and save the response. Examples use Windows PowerShell.

The REST and `scrape_html` interfaces return rendered HTML inside JSON. The optional retail agent extracts grounded product data and writes its compact PS5 result under `outputs/scrapes`; see `docs/RETAIL-AGENT.md`.

## 1. The API address and the website URL are different

| Purpose | Example |
|---|---|
| Local scraper API receiving your request | http://127.0.0.1:8000/scrape |
| Website you want Chrome to visit | https://example.com |

Send an HTTP POST to the local API and put the target website URL in the JSON body. Do not send requests directly to the private browser worker on port 8001.

The local API uses HTTP on loopback. Target websites must use HTTPS on port 443.

## 2. Start the containers

Prerequisites:

- Docker Desktop is running with Linux containers enabled.
- The repository is available locally.
- Your machine can download images and reach the target website.
- Docker has enough memory for Chrome; roughly 6 GB allocated to Docker is a useful starting point.

Host Python and Chrome are not required for the PowerShell examples: the API and browser run inside containers.

Open PowerShell in the repository. For this installation:

~~~powershell
Set-Location 'C:\Users\estev\OneDrive\Área de Trabalho\work\web_scraping'
~~~

Other users should replace that path with their repository folder.

Verify Docker:

~~~powershell
docker version
docker compose version
~~~

The first command must show both client and server information. Resolve any engine connection or permission error before continuing.

Prepare credentials and validate the configuration:

~~~powershell
.\scripts\setup.ps1
docker compose config --quiet
~~~

The setup script creates secrets/api_key.txt with a random API key and secrets/upstream_proxy.txt as an empty optional proxy setting. Existing files are preserved.

Build the shared API/egress image, then start all services:

~~~powershell
docker compose build api chrome
docker compose up -d --no-build --wait --wait-timeout 180
~~~

Only continue to the second command if the build succeeds. The first run may take several minutes to download images. Compose starts api, chrome, and egress.

Check status and browser readiness:

~~~powershell
docker compose ps
Invoke-RestMethod 'http://127.0.0.1:8000/readyz'
~~~

Expected readiness response:

~~~json
{"status": "ready"}
~~~

The containers continue running after you close PowerShell.

## 3. Send a URL using the provided script

From the repository folder:

~~~powershell
.\scripts\smoke.ps1 -Url 'https://example.com'
~~~

The script reads the API key, waits for browser readiness, sends the request, and saves each successful response to a new `outputs/scrapes/scrape_<UTC-timestamp>_<unique-id>.json` file. The timestamp records the execution start in UTC, including milliseconds; the unique ID distinguishes concurrent runs. Existing results are never overwritten. The script prints the full saved path. Failures continue to use the error logs described below.

Each run creates a new output file. The manual example below lets you choose filenames.

To try the original product page:

~~~powershell
.\scripts\smoke.ps1 -Url 'https://www.americanas.com.br/console-playstation-5-edicao-digital--825gb-%E2%80%93-astro-bot-4-e-gran-turismo-7/p'
~~~

Start with example.com to check the installation. A retailer may return a login screen, challenge, unavailable product, or other unexpected content.

## 4. Send the request manually with PowerShell

Keep the containers running and execute this from the repository folder:

~~~powershell
$apiKey = (Get-Content -Raw -LiteralPath '.\secrets\api_key.txt').Trim()

$body = @{
    url = 'https://example.com'
    wait_seconds = 5
    timeout_seconds = 30
} | ConvertTo-Json

$result = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/scrape' -Method Post -Headers @{ 'X-API-Key' = $apiKey } -ContentType 'application/json' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 180

$result | Select-Object title, final_url, html_bytes, browser_version, elapsed_ms
~~~

Change the url value to select a different website. Keep the API address pointing to your local scraper.

Save the complete JSON and the HTML separately:

~~~powershell
New-Item -ItemType Directory -Force -Path '.\outputs\scrapes' | Out-Null
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '.\outputs\scrapes\example-response.json' -Encoding utf8
$result.html | Set-Content -LiteralPath '.\outputs\scrapes\example-page.html' -Encoding utf8
~~~

The API returns data to the caller; it does not automatically save it on your host. These commands create the files.

## 5. Use any public website

`URL_POLICY=public` is now the default and is configured in the local `.env`. Any public HTTPS hostname on port 443 is accepted without registration, including Kabum, Americanas and Ponto Frio. `ALLOWED_HOSTS` is ignored in public mode.

Private IPs, localhost, cloud metadata endpoints, credentials in URLs and non-HTTPS schemes are still blocked. Requested and final URLs are validated; the proxy checks and pins public destination addresses for every connection, including intermediate redirects.

To optionally restrict requested/final hostnames, set `URL_POLICY=allowlist` and provide a nonempty `ALLOWED_HOSTS`. After changing environment settings, recreate the API. A terminal `URL_POLICY` variable overrides `.env`.

## 6. Request fields and response

Send these fields as JSON:

| Field | Required? | Default | Meaning |
|---|---|---|---|
| url | Yes | — | HTTPS page to visit; maximum 4,096 characters |
| wait_seconds | No | 5 | Extra settling delay after navigation/selector wait; 0–10 seconds |
| timeout_seconds | No | 30 | Timeout for navigation and, separately, the selector wait; 5–60 seconds |
| wait_css | No | null | CSS selector to wait for before the settling delay; maximum 500 characters |

Example for a page rendered with JavaScript:

~~~json
{
  "url": "https://example.com",
  "wait_css": "h1",
  "wait_seconds": 2,
  "timeout_seconds": 30
}
~~~

Choose a selector that exists on the target page. This checks element presence; it does not guarantee all background requests have finished. Allow up to 180 seconds on the HTTP client for browser startup, waits and cleanup.

Illustrative response:

~~~json
{
  "url": "https://example.com",
  "final_url": "https://example.com/",
  "title": "Example Domain",
  "html": "<html>...</html>",
  "html_bytes": 16,
  "browser_version": "152.0.7977.82",
  "fetched_at": "2026-09-19T12:00:00+00:00",
  "elapsed_ms": 6400
}
~~~

Actual values depend on the page. The html field contains the rendered DOM, including JavaScript changes present at capture time. html_bytes measures that string in UTF-8. fetched_at is UTC. elapsed_ms measures browser work before session cleanup.

HTTP 200 from the API means it captured a page. It does not prove that the website returned HTTP 200 or that the desired product exists. Inspect the title and HTML for errors, challenges, and login pages. The target website's HTTP status is not returned.

Only one scrape runs at a time, and request starts must be at least three seconds apart. Keep calls sequential and honor Retry-After when receiving HTTP 429.

## 7. Call the API from Python

Run this code from the repository folder in a Python script or notebook while the containers are running. It uses only the standard library:

~~~python
import json
from pathlib import Path
from urllib.request import Request, urlopen

api_key = Path("secrets/api_key.txt").read_text(encoding="utf-8").strip()
payload = {
    "url": "https://example.com",
    "wait_seconds": 5,
    "timeout_seconds": 30,
}

request = Request(
    "http://127.0.0.1:8000/scrape",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    },
    method="POST",
)

with urlopen(request, timeout=180) as response:
    result = json.load(response)

output = Path("outputs")
output.mkdir(exist_ok=True)
(output / "python-response.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(result["title"])
print(f"Received {result['html_bytes']} HTML bytes")
~~~

For Postman, create a POST request to http://127.0.0.1:8000/scrape, add X-API-Key using your local key, select Body → raw → JSON, and send the JSON example above. Keep the key in a private/local variable rather than an exported shared collection.

## 8. Daily operation

Show status and recent logs:

~~~powershell
docker compose ps
docker compose logs --tail 100 api chrome egress
~~~

Stop and remove the Compose containers/network:

~~~powershell
docker compose down
~~~

This preserves source files, credentials, downloaded images, and host output files.

Start again without rebuilding:

~~~powershell
docker compose up -d --no-build --wait --wait-timeout 180
~~~

After editing application code or dependencies, rebuild first:

~~~powershell
docker compose build api chrome
docker compose up -d --no-build --wait --wait-timeout 180
~~~

If port 8000 is occupied, choose another published port:

~~~powershell
$env:SCRAPER_API_PORT = '8001'
docker compose up -d --no-deps --force-recreate api
.\scripts\smoke.ps1 -BaseUrl 'http://127.0.0.1:8001' -Url 'https://example.com'
~~~

Update manual request addresses to port 8001 too. To persist the port across PowerShell sessions, put SCRAPER_API_PORT=8001 in your existing .env file.

## 9. Troubleshooting

### Updating an installation that used the allowlist

The local `.env` now contains `URL_POLICY=public`. To apply the code and configuration:

~~~powershell
docker compose build api chrome
docker compose up -d --no-build --wait --wait-timeout 180
.\scripts\smoke.ps1 -Url 'https://www.kabum.com.br/produto/989702/console-sony-playstation-5-ssd-825gb-controle-sem-fio-dualsense-2-jogos-digitais-edicao-digital'
~~~

If `host_not_allowed` persists, check whether your terminal defines `URL_POLICY=allowlist`, and ensure the API image was rebuilt and its container recreated. Paste a raw URL, not a Markdown link. Public mode removes the API's hostname restriction; website anti-bot challenges can still occur.

### Error storage and diagnosis

API errors return detail, code and request_id. The X-Request-ID response header matches the stored request ID. Codes distinguish host_not_allowed, dns_resolution_failed, non_public_destination, invalid_url and redirect_* failures. Authorization, validation, size limits, busy browser, browser timeouts and unexpected application failures are also logged.

The API writes JSON lines to /app/logs/errors.jsonl in the Compose error_logs named volume and to container stderr. Each entry contains a UTC timestamp, request ID, HTTP status, error code, safe message and endpoint. It deliberately omits URLs, query strings, keys, HTML and raw exception traces. The file rotates at 5 MB with five backups (approximately 30 MB maximum). Normal container recreation and docker compose down preserve this volume; docker compose down --volumes deletes it.

View current API errors:

~~~powershell
docker compose exec -T api python -c "from pathlib import Path; print(Path('/app/logs/errors.jsonl').read_text(encoding='utf-8'))"
~~~

Copy the current file and rotated backups to your machine:

~~~powershell
New-Item -ItemType Directory -Force .\logs\api | Out-Null
docker compose cp api:/app/logs/. .\logs\api
~~~

The smoke script also saves failures to logs/client-errors.jsonl on your machine, including connection failures when the API cannot log them. It prints the error code, guidance and request ID when the server supplies them. This file also rotates at 5 MB with five backups; run one smoke client at a time. Read it with:

~~~powershell
Get-Content .\logs\client-errors.jsonl -Tail 20
~~~

The logs directory is ignored by Git but may still synchronize through OneDrive. Previously unrecorded errors cannot be recovered. Logging begins after deploying this update. These files record API and smoke-client failures, not every failed browser asset or website HTTP status: a challenge or error page can still arrive as HTML with a successful scrape response. Infrastructure errors before the application (such as the HTTP server's concurrency limit) remain in container/platform logs.

Outside Compose, set ERROR_LOG_FILE to a writable persistent location to enable file storage; otherwise errors go only to stderr. Azure deployments need platform log collection/retention or a persistent mount configured separately. The local Docker volume does not automatically move to Azure.

| Symptom/status | What to check |
|---|---|
| Docker engine error | Start Docker Desktop with Linux containers and verify docker version works in your regular terminal. |
| Connection refused | Check container status, selected port, and API logs. |
| /readyz returns 503 | Chrome is not ready/reachable. Check Chrome logs and allow startup time. Readiness does not check the target website. |
| 400 | Check HTTPS/port 443, public DNS resolution, and host restrictions only if allowlist mode is enabled. |
| 401 | Read the current API key. If you changed its file, recreate both api and chrome to reload it. |
| 413 | Request exceeds 16 KB or encoded JSON response exceeds 3.5 MB. Oversized HTML is not silently truncated. |
| 422 | Check field names and bounds. Cookies, login credentials, caller-selected proxies, and arbitrary JavaScript are not accepted. |
| 429 | Browser busy or request starts too close together. Honor Retry-After. |
| 502 | Browser operation failed. Check Chrome logs and target connectivity. |
| 503 during requests | HTTP server concurrency limit may have been reached. Reduce parallel calls. |
| 504 | Navigation or selector wait timed out. Confirm the URL/selector; increase timeout_seconds up to 60 if needed. |
| 404 at / or /docs | Expected: use /healthz, /readyz, and POST /scrape. Interactive API docs are disabled. |
| 200 with unexpected HTML | Inspect the title/content for a challenge, login screen, or website error. |

Plain HTTP assets are blocked by the proxy and may cause some pages to render incompletely. The isolated browser worker uses undetected-chromedriver with version_main=152. Selenium remains a dependency of that package; Selenium Grid is not used.

Your host Chrome version does not control the browser version inside Docker.

## 10. On-premises access and automated tests

The default configuration serves clients on the **same machine** through 127.0.0.1. Another machine on your office network cannot access this address. Localhost inside another container also refers to that container, not automatically to this API.

Serving other on-premises machines requires a separate network deployment configuration with a suitable host binding, authenticated HTTPS endpoint, and firewall rules. This guide does not enable LAN access.

Keep keys and scraped HTML private. Git ignores local secrets and outputs, but a repository inside OneDrive can still synchronize those files.

For an isolated automated container test, use the existing project virtual environment:

~~~powershell
.\.venv\Scripts\python.exe scripts\container_ci.py
~~~

Alternatively, use python scripts/container_ci.py with an installed Python 3.12+ interpreter. This runner requires no third-party Python packages.

The CI runner creates a separate temporary stack, key, and port, runs checks, and removes its stack afterward. It does not leave the everyday API server running. Use section 2 to start that server.

See docs/CI.md for unit tests, CI, and future AI/agent evaluation guidance.
