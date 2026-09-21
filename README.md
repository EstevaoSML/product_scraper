# Rendered HTML scraper

Agents can use the authenticated `scrape_html` MCP tool at `/mcp` (official Python SDK 2.2.0, protocol 2026-07-28 with legacy HTTP compatibility). See [MCP setup and client example](docs/MCP.md). REST and MCP share browser limits, URL policy and error storage.

CI and regression testing are described in [docs/CI.md](docs/CI.md). Run `python scripts/container_ci.py` from a normal terminal to build and check an isolated Docker stack. GitHub Actions runs unit/security checks, dependency auditing, and the same container smoke test on pushes and pull requests. `AGENTS.md` and the PR checklist remind contributors to add tests; future AI/agent evaluations are explicitly marked as not yet implemented.

Send an HTTPS URL to `POST /scrape`; receive JSON containing the rendered HTML, title, final URL, browser version, size, and timestamp. Each call opens a fresh browser session and closes it afterward. There is no LLM processing in this first version.

## Local start (Windows PowerShell)

Install/start Docker Desktop with Linux containers and the WSL2 backend. Give Docker roughly 6 GB RAM for this stack. Host Chrome and your notebook virtual environment are not used by the containers.

```powershell
Set-Location 'C:\Users\estev\OneDrive\Área de Trabalho\work\web_scraping'
.\scripts\setup.ps1
docker compose config --quiet
docker compose up --build -d
.\scripts\smoke.ps1
```

The first run builds a custom browser image. The smoke script waits for the UC worker, requests `https://example.com`, and writes `outputs/scrape_<UTC-timestamp>_<unique-id>.json`. If PowerShell blocks scripts, review the files and invoke them with a process-scoped policy according to your machine's policy. Do not weaken the system-wide policy.

To try your product page:

```powershell
.\scripts\smoke.ps1 -Url 'https://www.americanas.com.br/console-playstation-5-edicao-digital--825gb-%E2%80%93-astro-bot-4-e-gran-turismo-7/p'
```

Or send a request yourself:

```powershell
$key = (Get-Content -Raw .\secrets\api_key.txt).Trim()
$body = @{
    url = 'https://example.com'
    wait_seconds = 5
    timeout_seconds = 30
    # wait_css = 'h1'  # Optional: wait for a known element before the settling delay.
} | ConvertTo-Json
$result = Invoke-RestMethod 'http://localhost:8000/scrape' -Method Post `
    -Headers @{'X-API-Key'=$key} -ContentType 'application/json' -Body $body -TimeoutSec 180
$result.html
```

Illustrative response (values depend on the page):

```json
{
  "url": "https://example.com",
  "final_url": "https://example.com/",
  "title": "Example Domain",
  "html": "<html>...</html>",
  "html_bytes": 1256,
  "browser_version": "152.0.7977.82",
  "fetched_at": "2026-09-16T12:00:00+00:00",
  "elapsed_ms": 6400
}
```

`200` means the rendered page was captured. It does **not** prove the target returned HTTP 200 or that a product was found: error pages, login screens, and CAPTCHA pages can also be captured. This API does not expose the upstream HTTP status. Verify expected content before downstream extraction. The browser backend uses `undetected-chromedriver` 3.5.5 with Chrome major version 152, matching the notebook approach. This does not guarantee access to every website or solve IP-reputation blocking.

## Container choice

`Dockerfile.browser` builds a custom Linux AMD64 worker from `selenium/standalone-chrome:152.0.7977.82-20260905`, retaining its matching Chrome/ChromeDriver 152 binaries. Its entrypoint runs the private UC worker instead of Selenium Grid. The API uses Python 3.12.14; the worker uses the base image's Python in a dedicated virtual environment. Selenium remains a library dependency of [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver).

Each request uses `uc.Chrome(options=options, version_main=152)` with a private writable copy of the supplied ChromeDriver, an isolated profile, and the existing proxy. No driver is downloaded during requests. The worker terminates the complete job process group after success, failure or a 150-second job deadline. This includes Chrome processes left by failed initialization. Readiness checks worker availability and executable presence; use the container CI test to verify an actual browser launch.

After upgrading from the Grid backend, rebuild **both** images:

```powershell
docker compose build api chrome
docker compose up -d --no-build --wait --wait-timeout 180
```

The response includes `browser_backend: "undetected-chromedriver"`. The existing request format, URL policy, API key and persistent error volume remain in use. The browser worker also reads the API key: recreate both `api` and `chrome` when rotating it. `requirements-browser.txt` pins UC and includes setuptools to provide its distutils compatibility on Python 3.12+.

The versioned tag makes upgrades deliberate. Before production, scan both images and record/pin their registry digests; dated tags are not a cryptographic immutability guarantee. Update Chrome and the matched driver together when applying browser security updates. Python dependencies are constrained to the tested versions.

## Configuration and errors

The default `URL_POLICY=public` accepts any public HTTPS site on port 443, including Kabum and new retailers, without listing domains. Existing `ALLOWED_HOSTS` values are ignored in this mode. Private, loopback, link-local and cloud metadata destinations remain blocked, including redirects and connections through the egress proxy. Credentials in URLs and non-HTTPS schemes remain rejected.

For optional exact-host restrictions, set `URL_POLICY=allowlist` and a nonempty comma-separated `ALLOWED_HOSTS` in `.env`. Unknown policy modes fail startup. Rebuild/recreate the API after code changes; environment-only changes require recreation. Terminal environment variables override `.env`.

Accepted fields: `url`, `wait_css` (optional CSS selector), `wait_seconds` (0–10, default 5), and `timeout_seconds` (5–60, default 30). The timeout applies separately to navigation and selector waiting. The HTTP client should allow up to 180 seconds including browser startup/cleanup. Use a known selector for JavaScript pages; a fixed delay cannot guarantee all dynamic content loaded.

| API status | Meaning |
|---|---|
| 400 | URL/DNS/final redirect rejected |
| 401 | Missing/incorrect API key |
| 413 | Request exceeds 16 KB or encoded JSON response exceeds 3.5 MB |
| 422 | Unknown fields or invalid field values |
| 429 | Browser busy or requests less than three seconds apart; honor Retry-After |
| 502 | UC/browser operation failed |
| 503 | Browser not ready, or HTTP server concurrency limit reached |
| 504 | Navigation/selector timed out |

`GET /healthz` checks the API. `GET /readyz` checks private browser worker availability. Readiness does not guarantee the target website or an upstream proxy is reachable. One Uvicorn worker and one browser session are intentional; additional workers would bypass the process-local limit.

## Proxy and security design

Compose connects Chrome only to an internal network. API and `egress` also have an internet network: the API needs public DNS for URL validation, which Docker does not forward from internal-only networks. The API consequently has outbound network access; only Chrome's direct internet route is isolated. Chrome uses the gateway for HTTPS requests. The gateway resolves each destination, rejects loopback/private/link-local/reserved addresses (including cloud metadata), and connects to the checked IP literal to avoid a second DNS lookup. Redirects and HTTPS subresources pass through the same checks. Chrome's implicit localhost proxy bypass is disabled. Plain HTTP requests/assets and ports other than 443 are denied, which can affect older sites. TLS certificates are still verified by Chrome; the gateway does not decrypt page traffic.

Only `127.0.0.1:8000` is published locally. Browser worker (8001), proxy (8080), and VNC ports are not published. API and gateway run as non-root with read-only filesystems, dropped capabilities, resource limits, and no-new-privileges. The API requires a random key, avoids access logs and detailed error reflection, disables caching, limits request/response sizes, and accepts no caller-supplied cookies, credentials, proxy settings, or JavaScript.

The Chrome configuration uses `--no-sandbox` for container compatibility and `--disable-dev-shm-usage` for the Azure configuration. Containers and network policy therefore matter; this is not a strong isolation boundary against a browser exploit. Do not attach host folders, Docker sockets, sensitive networks, or privileged identities to the browser. The proxy is a small bounded CONNECT gateway, not a production WAF or a defense against every possible hostile browser behavior. Its 64 concurrent tunnels, 120-second tunnel lifetime, and 64 MB per-tunnel limit are operational bounds, not a total page download quota.

Secret files are ignored by Git and excluded from the Docker build context. Local Compose secrets are still plaintext files. **This repo is inside OneDrive**, so those files may sync: use a throwaway local key, restrict access, and use Key Vault for cloud credentials. Rotate the local key by replacing its file and recreating both the API and Chrome worker. Scraped HTML can contain personal data or tokens; avoid logging it and set a retention policy for saved outputs. Treat HTML as untrusted data when later sending it to an LLM.

### Optional external proxy

Start without an external provider. For a stable outbound IP, prefer a dedicated proxy or an Azure NAT Gateway appropriate to your network. Use a reputable provider with a documented retention policy; avoid free shared proxies. Match the site region when needed, keep a stable session, keep rates low, and honor access restrictions and retry guidance. A proxy does not guarantee access or make a blocked page successful.

The built-in gateway can chain through an HTTP CONNECT proxy. Put the URL in `secrets/upstream_proxy.txt` (empty means direct internet):

```text
http://username:percent-encoded-password@proxy.example.net:8080
```

```powershell
docker compose restart egress
```

The provider **must support CONNECT to an IP address**, because the gateway pins the destination IP rather than delegating destination DNS to the provider. Confirm this before buying a plan. Destination DNS runs at this gateway, so geo-DNS results may reflect its location rather than the provider's exit region. Only HTTP upstream proxies are implemented; SOCKS, HTTPS-to-proxy, PAC, and automatic rotation are not. An HTTP upstream exposes the Basic proxy credentials to the network between gateway and proxy: use it only over a trusted private connection or VPN. The tunneled website HTTPS remains encrypted. Never put proxy credentials into the API request or Chrome command-line flags.

## Azure Container Apps deployment

The included ARM template `deploy/main.json` runs API, Chrome, and gateway as three containers in one Container App, with a total of 2 vCPU / 4 GiB, HTTPS ingress only on the API, Key Vault-backed authentication, health probes, and at most one replica. It scales to zero, so a first call can be slow. For scheduled ingestion with predictable latency, set `minReplicas` to 1 (ongoing compute cost).

This is an Azure Container Apps service, not an Azure Functions image. Fabric orchestrates requests to it; do not try to upload the Compose file as a Fabric notebook environment. Azure supports [multiple containers in one app](https://learn.microsoft.com/en-us/azure/container-apps/containers), and the deployment uses its [ARM schema](https://learn.microsoft.com/en-us/azure/container-apps/azure-resource-manager-api-spec).

**Cloud isolation difference:** containers in the same Container App share networking. The template uses `localhost` for the UC worker and the gateway and does not reproduce Compose's internal network barrier. Proxy validation still runs for configured browser traffic, but it is not an enforced egress boundary against bypass or browser compromise. For untrusted/multi-tenant use, separate the browser into its own network and enforce firewall/UDR egress through a separately hosted gateway. Also separate the identity with Key Vault access from the browser workload. Review this before exposing the service to additional users. The supplied template is an authenticated single-operator starting point.

After local smoke tests pass, prepare these existing Azure resources in your subscription:

1. Resource group and Container Apps environment in the intended region.
2. Azure Container Registry (ACR).
3. User-assigned managed identity with `AcrPull` on that registry (for an ACR using standard RBAC registry permissions).
4. Key Vault containing a new random secret named `scraper-api-key`, at least 32 characters; give the identity `Key Vault Secrets User` and ensure vault networking permits the app to retrieve it.
5. Azure CLI with Container Apps support and permission to build in ACR/deploy to the resource group. Role assignments and network permission changes may take time to propagate.

Build the API remotely in ACR from this repository (this incurs Azure usage):

```powershell
az acr build --registry YOUR_ACR_NAME --image html-scraper:v1 .
az acr build --registry YOUR_ACR_NAME --image html-scraper-browser:v1 --file Dockerfile.browser .
Copy-Item .\deploy\parameters.example.json .\deploy\parameters.local.json
```

Edit `parameters.local.json` with your resource IDs, registry login server, API image reference, and Key Vault **secret URL**, never the secret value. Build Dockerfile.browser, push the resulting custom image to your ACR, and set the required `chromeImage` parameter to that image reference. The public Selenium image alone cannot run the UC worker.

```powershell
az deployment group validate --resource-group YOUR_RESOURCE_GROUP `
    --template-file .\deploy\main.json --parameters '@deploy/parameters.local.json'
az deployment group create --resource-group YOUR_RESOURCE_GROUP `
    --template-file .\deploy\main.json --parameters '@deploy/parameters.local.json'
```

The deployment output contains the HTTPS endpoint. Test `/readyz`, then call `/scrape` using the cloud Key Vault key. The ARM file contains no secret value. It does not create the prerequisite resources, assign roles, configure private ingress/egress, or purchase a proxy. The cloud gateway is direct by default; to chain a proxy, add a second Key Vault-backed secret and map it to `UPSTREAM_PROXY_URL` on `egress` only.

For multiple clients, add Entra authentication/API Management and central rate limits. The app currently validates `X-API-Key`, not Entra access tokens. Retain the shared key until the application authentication is deliberately changed. There is no deployment executed by this repository setup.

## Microsoft Fabric

Preferred first integration: paste `examples/fabric_notebook.py` into a **PySpark notebook with a Lakehouse attached**, fill in the API and vault addresses, and grant the notebook's executing identity permission to read that secret. The example gets the key via [NotebookUtils credentials](https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-utilities) and saves JSON under `/lakehouse/default/Files/scrapes/` without printing HTML or the key.

Alternatively use a Fabric pipeline **Web activity**: POST to `/scrape`, set `Content-Type: application/json` and `X-API-Key` from your secure configuration, and use `{"url":"https://example.com","wait_seconds":5}` as the body. Enable secure input/output handling for credentials and captured content. Never store a literal key in an exported pipeline definition.

Fabric's [Web activity response limit is 4 MB](https://learn.microsoft.com/en-us/fabric/data-factory/web-activity). The API limits the actual UTF-8 encoded JSON to 3.5 MB and returns 413 instead of silently truncating HTML. If larger pages become necessary, extend the API to store results in Blob/ADLS and return a small object reference; that storage mode is not yet implemented. A notebook also remains subject to this API's 3.5 MB limit.

## Checks and troubleshooting

```powershell
python -m venv .venv-api
.\.venv-api\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv-api\Scripts\python.exe -m pytest -q
docker compose ps
docker compose logs --tail 100 api chrome egress
docker compose down
```

The `checks/` name avoids the notebook repo's existing `*test*` ignore rule. Unit and local proxy integration checks do not establish that Americanas allows this browser. A real container smoke test is still required. Treat container logs as potentially sensitive when troubleshooting even though API access logging is disabled.

If the site renders a block page, inspect the JSON content and stop/retry according to the site's policy; don't assume a proxy or a different delay will solve it. A blank/partial page may require a specific `wait_css`, more settling time, or may depend on blocked plaintext HTTP resources. DNS/proxy errors appear as browser errors or browser-rendered error content. Memory-related browser crashes require more Docker RAM, fewer competing workloads, or adjustments to the Chrome memory limit.

## Agent navigation and Azure Container Apps

The scraper now exposes session-based MCP navigation: open, inspect, search, follow links and close. See [the agent guide](docs/AGENT-NAVIGATION.md) for tool payloads, evidence-backed product reports and the provider-neutral decision loop. Your agent supplies the LLM; the server enforces navigation and session budgets.

Use [the Container Apps deployment guide](deploy/terraform/README.md) for Terraform, Key Vault, managed identities and migration from the earlier Functions design. The current Terraform deploys no Azure Function. Existing `/scrape` and `scrape_html` remain available when no navigation session owns the browser.


## Azure DevOps delivery and cloud error logs

[Azure DevOps CI/CD setup](deploy/azure-devops/README.md) documents the Terraform bootstrap, protected pipelines, workload identity federation, approval-gated deployment and post-deployment Log Analytics verification. Container errors include severity, service, revision and request ID; use the saved workspace queries installed by the application Terraform.
