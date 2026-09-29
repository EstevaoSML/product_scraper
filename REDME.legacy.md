# Legacy infrastructure and scraper guide

This is the archived guide for the original scraper and ACR-based Azure infrastructure. For the current low-budget portfolio deployment, follow [README.md](README.md). These stacks use separate Terraform state; the commands below are not the portfolio deployment procedure. Infrastructure source files remain in their original directories.

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

The first run builds a custom browser image. The smoke script waits for the UC worker, requests `https://example.com`, and writes `outputs/scrapes/scrape_<UTC-timestamp>_<unique-id>.json`. Local PS5 agent runs write an ordered JSON array under `outputs/scrapes`, with one `url/has_ps5_info/ps5_info` object for every page assessed by the Agent; agent reports remain in `outputs/agent`. Product images are not collected or sent to the Agent. If PowerShell blocks scripts, review the files and invoke them with a process-scoped policy according to your machine's policy. Do not weaken the system-wide policy.

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

The supported cloud deployment uses Terraform under `deploy/terraform`. It creates the Container Apps environment, separate API and browser applications, ACR, managed identities, Key Vault integration, private networking and Log Analytics. Follow the [Container Apps deployment guide](deploy/terraform/README.md).

## Microsoft Fabric

Use a Fabric pipeline **Web activity**: POST to `/scrape`, set `Content-Type: application/json` and `X-API-Key` from your secure configuration, and use `{"url":"https://example.com","wait_seconds":5}` as the body. Enable secure input/output handling for credentials and captured content. Never store a literal key in an exported pipeline definition.

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

## GPT-5 mini retail research agent

See [docs/RETAIL-AGENT.md](docs/RETAIL-AGENT.md) for the bounded agent, Azure Container Apps Job, private reports and synthetic evaluations. Infrastructure is optional through `enable_research_agent`; running a model always requires an explicit spending cap.

---

# Azure Container Apps deployment

The current Terraform stack hosts the MCP API directly in **Azure Container Apps**. There is no Azure Function, Functions storage or Functions deployment step.

```mermaid
flowchart LR
  Agent -->|Public HTTPS / client key| API[Container App: MCP API]
  API -->|Internal HTTPS / backend key| Browser[Container App: browser worker + egress]
  Browser -->|Public HTTPS proxy| Retailer
  API -->|Managed identity / private endpoint| KV[Key Vault]
  Browser -->|Managed identity / private endpoint| KV
  API --> Logs[Log Analytics]
  Browser --> Logs
```

The API app accepts public HTTPS. The browser app has **internal ingress on port 8001**, so only apps inside this Container Apps environment can address it. The environment uses an external load balancer for the public API. [Azure ingress visibility](https://learn.microsoft.com/en-us/azure/container-apps/ingress-how-to).

## Resources and access

Terraform creates a resource group, VNet/delegated subnet, Container Apps environment, two container apps, ACR, two managed identities, Key Vault with private endpoint/DNS, Log Analytics and Key Vault audit diagnostics. Logs retain 30 days by default.

| Secret | Access |
| --- | --- |
| `mcp-api-key` | Agent authenticates to public API; API identity can read it |
| `scraper-api-key` | API authenticates to browser; API and browser identities can read it |

Both identities have AcrPull. The browser identity cannot read the client key. A designated secret operator has Key Vault Secrets Officer. Terraform stores secret references, never secret values; no secret-value resources/data sources are used. ACR admin and anonymous access are disabled. Key Vault uses RBAC, private access by default, purge protection and 90-day soft deletion.

Both apps use min/max replicas 1. A browser lease persists across agent tool calls on that worker, and reserves its single browser slot for at most 180 seconds. API requests can arrive at any time but receive a busy response while another lease owns Chrome. Do not scale the backend beyond one replica without session routing and external lease coordination. Revisions/restarts invalidate sessions; schedule deployments and secret rotations as maintenance and restart the agent's research afterward.

## 1. Prerequisites and Terraform state

Install Terraform >=1.9 (CI uses 1.13.5), Azure CLI and Az.Accounts/Az.KeyVault PowerShell modules. The provider is locked to AzureRM 5.6.0. Select a region with Container Apps Consumption capacity; the example defaults to Brazil South.

The deployment operator needs resource creation and role-assignment privileges (for example Contributor plus User Access Administrator). Image builds need ACR Tasks permission. Create a Cost Management budget: two always-running apps, the registry, private endpoint and monitoring are billable.

```powershell
az login
az account set --subscription '<subscription-id>'
az ad signed-in-user show --query id -o tsv
```

Before Terraform, create a separate state StorageV2 account/private `tfstate` container through your organization bootstrap process or Azure Portal. Disable anonymous access/shared keys, require TLS 1.2, enable blob versioning and soft deletion, and grant the runner Storage Blob Data Contributor on the state container. Restrict networking to the runner; private endpoints require VNet connectivity and DNS. Do not create the state account inside the same state it must hold. [Azure Terraform backend authentication](https://developer.hashicorp.com/terraform/language/backend/azurerm).

Copy/edit the examples in `deploy/terraform`. `backend.hcl` points to that pre-created state account and uses Entra authentication. Treat state and saved plans as sensitive even without application passwords; keep their working directory outside shared/OneDrive folders for a production deployment.

## 2. Foundation

```powershell
# From repository root:
Set-Location deploy/terraform
Copy-Item terraform.tfvars.example terraform.tfvars
Copy-Item backend.hcl.example backend.hcl
# Edit both files before proceeding.
terraform init -backend-config=backend.hcl
terraform fmt -check -recursive
terraform validate
terraform plan -out=foundation.tfplan
# Review changes and costs before applying:
terraform apply foundation.tfplan
```

Keep `deploy_workloads=false`. Set `subscription_id`, a globally unique lowercase name (6–16 letters/digits, starting with a letter), and the operator's Entra **object ID** in `secret_operator_object_id`.

To seed secrets from your workstation, temporarily set `operator_ipv4_cidrs=["YOUR.PUBLIC.IP/32"]`. This opens Key Vault only to that IP and still requires RBAC. Alternatively leave it empty and seed from a VNet-connected administrator. Wait for role propagation if initially denied.

## 3. Secrets and images

From the repo root:

```powershell
Connect-AzAccount
Set-AzContext -Subscription '<subscription-id>'
.\scripts\initialize_azure_keys.ps1 -VaultName 'kv-YOURNAME'
az acr build --registry YOURNAMEacr --image html-scraper:v2 --file Dockerfile .
az acr build --registry YOURNAMEacr --image html-scraper-browser:v2 --file Dockerfile.browser .
```

The initialization script generates two independent 256-bit secrets, never prints them and preserves existing secret names. Do not pass values in Terraform variables or build arguments. Use new image tags for every release, then set `api_image_tag` and `browser_image_tag` to those tags.

## 4. Workloads

Set `deploy_workloads=true`. Remove temporary vault access (`operator_ipv4_cidrs=[]`) once the secrets exist.

```powershell
Set-Location deploy/terraform
terraform plan -out=workloads.tfplan
terraform apply workloads.tfplan
terraform output
```

There is **no** `func publish` step. ACR image deployment runs the existing `app.main:app` API and `app.browser_worker:app` worker. Role propagation may delay initial pulls/Key Vault resolution; inspect Container Apps system logs and retry after propagation rather than copying plaintext keys into settings.

Use the exact `mcp_endpoint` output. Configure the agent for Streamable HTTP and the `X-API-Key` header containing `mcp-api-key`, supplied by the agent platform's secret store. Never give the agent the backend key or managed-identity credentials. A browser visit without the header should return 401.

Check API `/readyz`, MCP tools/list, then open/inspect/close a session on a public fixture you control. Verify invalid keys and private addresses fail. See [agent navigation usage](docs/AGENT-NAVIGATION.md) for searching and extracting PS5 details.

## Migration from the previous Functions Terraform

No deployment has been performed by this coding task. If you already applied the old stack yourself, **do not blindly apply this configuration**. Back up remote state and inspect a saved plan. It removes the Function, Functions plan/storage and old identities; changing environment load-balancer type can replace the environment and its dependent apps. It also changes the backend from a three-container REST API app to a two-container private browser worker.

Use a new globally unique name and a separate backend state key for a parallel rollout when avoiding downtime. Seed new vault secrets, build images, validate the new MCP endpoint, switch agents, then separately review retirement of the old stack. Existing browser sessions cannot migrate.

## Logs, rotation and maintenance

Container Apps captures stdout/stderr for both apps into Log Analytics. Existing structured error records include request IDs without HTML, keys or raw exception contents. Workstation error files remain local; cloud logs are in Azure Monitor. Scraped HTML is not automatically archived in Azure. The agent CLI writes timestamped research reports on the agent host.

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1h)
| where Log_s contains "REQUEST-ID"
| project TimeGenerated, ContainerAppName_s, Log_s
```

Create alerts for repeated failures, unhealthy replicas, Key Vault denials and budget thresholds. Restrict log-reader rights and avoid logging page bodies or credentials.

Rotate secrets by creating new versions through an authorized VNet-connected operator. Versionless references are used: Container Apps checks for new versions within 30 minutes and restarts active revisions whose environment variables reference the changed secret. Coordinate maintenance and verify both apps have refreshed before resuming research. Rotate `mcp-api-key` in agents too; rotating `scraper-api-key` affects both apps. Old/new simultaneous acceptance is not implemented. [Container Apps secret rotation](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets).

## Limits and further hardening

This stack uses one shared client key and one browser lease; it is not a multi-tenant service. Session ownership maps to that shared credential. Add Entra authentication with per-principal ownership, authorization and quotas before exposing it to unrelated users. API Management and Defender image scanning are optional next steps, not resources created here.

The private browser app and egress sidecar share networking and a managed identity. Chrome is configured to use the validating proxy, but Azure does not enforce Compose's isolated Chrome network. A browser exploit could bypass that proxy or reach the app identity. For stronger isolation, use a dedicated identity-free browser segment plus enforced outbound firewall rules; internal ingress alone is not outbound isolation.

Website actions use conservative search/link heuristics, public HTTPS/DNS policy and bounded execution. They are not a guarantee against every site's server-side effects or prompt injection. The external agent must treat all site content as untrusted. No login/checkout or arbitrary-script tool is provided.

Review `terraform plan -destroy` before removal. Key Vault purge protection intentionally prevents immediate reuse of a deleted vault name. CI's mocked Terraform tests establish configuration invariants, not a live Azure acceptance test.


## Automated delivery

Use [the Azure DevOps bootstrap](deploy/azure-devops/README.md) after the foundation and secret initialization. The application provider now disables automatic resource-provider registration so scoped pipeline identities can run it. Have a subscription administrator register Microsoft.App, Microsoft.ContainerRegistry, Microsoft.KeyVault, Microsoft.ManagedIdentity, Microsoft.Network, Microsoft.OperationalInsights, Microsoft.Insights and Microsoft.Storage once before provisioning.

Application and worker errors now carry severity/service/revision metadata. Terraform installs saved searches for structured errors and Container Apps platform failures. The CD pipeline verifies actual ingestion by request ID after applying an approved plan.

## Optional retail research job

Set `enable_research_agent=true` to add the isolated GPT-5 mini agent, identity, private Blob reports/lease and manual Container Apps Job. Prepare `openai-api-key` in Key Vault and the `Dockerfile.agent` image before workload deployment. Use `agent_image_tag` for its release. Existing browser/API identities receive no model key. See [the agent runbook](docs/RETAIL-AGENT.md).
