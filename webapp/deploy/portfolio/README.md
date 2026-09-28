# Azure portfolio deployment — no ACR

This is the low-budget deployment for **10 products × 4 retailers each month**. It is a separate Terraform stack. The existing `webapp/deploy/terraform` production stack is preserved; do not apply this configuration to its state.

All scheduled execution, browser scraping, agent calls, secrets, website hosting and durable files run on Azure. Your computer only packages source and runs deployment commands. OpenAI remains the external model provider already used by this project. There is no GitHub Actions runner, external container registry account, ACR resource, or custom image build.

## Resources and data flow

| Resource | Purpose |
|---|---|
| Resource group | Isolates the portfolio and its cost alert |
| StorageV2 Standard LRS, ADLS Gen2 enabled | Private `catalog` container with observations, evidence, monthly budget ledgers and execution status; private `packages` container with application/browser ZIPs |
| Storage static website (`$web`) | Public HTML, CSS, JavaScript and illustrative product images only; search, history and retailer details work in the browser |
| Container Apps Consumption environment | Hosts one job; no dedicated workload profile, custom VNet, NAT gateway or private endpoint |
| One Container Apps Job | Starts manually for the first test; optionally runs on the **first day of each month at 09:00 UTC (06:00 Brasília)**; 2 vCPU / 4 GiB, one replica, no automatic retries, 4-hour execution timeout |
| User-assigned managed identity + RBAC | Reads the private package and Key Vault; writes catalog and public site without storage account keys |
| Dedicated Key Vault Standard | Holds `openai-api-key`; no secret values in Terraform variables, state or command arguments |
| Monthly resource-group budget | Alerts at 50% and 100% of 8 **billing-currency units**; it does not stop spending |
| Storage lifecycle policy | Deletes raw reports after 90 days and old catalog snapshots after 30 days; preserves latest catalog with full price history, monthly ledgers, images and packages |

The job uses Microsoft's public `mcr.microsoft.com/playwright/python:v1.63.0-noble`, pinned by digest. **Public container image** means Microsoft's reusable runtime is downloadable; your application ZIP, research reports and secrets remain private. The SHA-256 of the ZIP is pinned in Terraform, and bootstrap verifies it before extraction/execution. The ZIP contains the application, initial catalog, existing illustrations and a matching Chrome/ChromeDriver 154.0.8037.57 pair obtained from Google's official distribution. Python dependencies install into an ephemeral venv at job startup.

The existing Flask template is rendered into static HTML **inside the Azure job**. Static hosting replaces the always-running Flask HTTP server for this deployment; local Flask development and the previous production deployment still work. Azure Storage was chosen instead of Static Web Apps so the same managed identity can publish directly without deployment tokens or an external CI service. Blob static hosting has small usage charges; it is not a fixed-price/free SWA plan.

## Expected monthly cost

For a small LinkedIn demo with low traffic and approximately 1 GB or less of retained files:

| Item | Planning estimate, USD/month |
|---|---:|
| Agent research: 40 × maximum $0.05 | At most $2 in application reservations |
| Image generation | $0 — disabled; existing five images are reused |
| Container compute | Usually $0 within the subscription's unused Consumption free grant |
| Storage, transactions, Key Vault and light website traffic | Allow $0.50–$2 |
| ACR, always-on web/API/browser, private endpoints, Log Analytics ingestion | $0 — not provisioned by this stack |
| **Expected portfolio total** | **Approximately $2.50–$4** |

This is an estimate, **not an enforceable $10 Azure spending cap**. Tax, region, subscription currency, other workloads consuming the shared free grant, repeated manual executions, package accumulation and public traffic can change the bill. Azure's cost budget alerts are delayed notifications, not a shutdown. The OpenAI bill is separate from Azure's cost budget; configure provider-side billing controls as well.

At the four-hour job timeout, one execution uses at most 28,800 vCPU-seconds and 57,600 GiB-seconds, below the published monthly free grant of 180,000 vCPU-seconds / 360,000 GiB-seconds **when that grant is otherwise unused**. Initialization and failed/manual runs also consume compute. Keep the number of package versions small; delete an old private package only after it is no longer referenced by the job. Do not delete monthly ledgers to retry failed research.

Sources, checked 2026-09-27: [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing), [pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/), [Storage static website hosting](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-static-website-host), [Azure budget behavior](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets), [Microsoft Playwright Python images](https://playwright.dev/python/docs/docker).

## First deployment and no-model test

Use PowerShell in the repository root. Prerequisites: Python 3.12+, Terraform 1.10+, Azure CLI with Container Apps commands, and an **active Azure subscription**. Logging in with `--allow-no-subscription` alone is insufficient for deployment. The operator needs resource creation and role-assignment rights (for example Owner on the chosen subscription/scope), and permissions to create cost budgets. Microsoft.App, Microsoft.Storage, Microsoft.KeyVault, Microsoft.ManagedIdentity and Microsoft.Consumption must be registered; the AzureRM provider can register required providers if your account permits it.

```powershell
cd 'C:\Users\estev\OneDrive\Área de Trabalho\work\web_scraping'
az login
az account list --query "[].{name:name,id:id,state:state}" -o table

python -m venv .venv-deploy
.\.venv-deploy\Scripts\python.exe -m pip install -r webapp/requirements-deploy.txt
Copy-Item webapp/deploy/portfolio/config.example.json webapp/deploy/portfolio/config.local.json
```

Edit `config.local.json`: subscription ID, a **globally unique** 6–16 character lowercase name, region and real alert/suggestion email addresses. It contains no secrets and is git-ignored. East US is the default for this small demo; change it if required by subscription capacity or data-location preferences.

```powershell
# Local Terraform validation only; no login/config/OpenAI key needed.
.\webapp\deploy\portfolio\deploy.ps1 -Action Validate

# Creates only this new stack, uploads the private package and creates a manual job.
.\webapp\deploy\portfolio\deploy.ps1 -Action Deploy -Python .\.venv-deploy\Scripts\python.exe

# Runs the real browser against example.com, seeds 10 products and publishes the page.
# No OpenAI calls. The script waits for Azure execution success and prints the URL.
.\webapp\deploy\portfolio\deploy.ps1 -Action Smoke -Python .\.venv-deploy\Scripts\python.exe
```

`Deploy` uses two saved Terraform plans: foundation, then workload after package upload. It applies them, creates Azure resources, and leaves the monthly schedule **disabled**. It never deletes or modifies a different deployment/state. On updates it pauses this stack's schedule, preserves its existing state/history and requires the new package to pass Smoke before resuming collection. Review the Terraform output. Azure RBAC sometimes takes several minutes to propagate; package upload retries only authorization propagation failures. If a Terraform data-plane operation fails during propagation, rerun Deploy with the same name and state.

The initial page contains the existing five observed products and five additional selected products awaiting evidence. New prices/history/images are **not fabricated**. Five new illustrations are intentionally not generated under this reduced budget. `Publish` republishes the last durable catalog without launching Chrome or calling the model (the shared bootstrap still prepares the runtime).

## Store the key, test one pair, then schedule

```powershell
# Secure interactive prompt, stored directly in the dedicated Azure Key Vault.
.\webapp\deploy\portfolio\deploy.ps1 -Action SetSecret -Python .\.venv-deploy\Scripts\python.exe

# Up to $0.05; first unattempted product/store pair in the UTC month.
.\webapp\deploy\portfolio\deploy.ps1 -Action CollectOne -Python .\.venv-deploy\Scripts\python.exe

# Remaining unattempted pairs this month, up to $2 INCLUDING CollectOne.
.\webapp\deploy\portfolio\deploy.ps1 -Action CollectMonthly -Python .\.venv-deploy\Scripts\python.exe

# Future monthly automatic runs; verifies current package passed Smoke and key exists.
.\webapp\deploy\portfolio\deploy.ps1 -Action EnableSchedule -Python .\.venv-deploy\Scripts\python.exe

# Stop future scheduled triggers without deleting data or the public website.
.\webapp\deploy\portfolio\deploy.ps1 -Action DisableSchedule -Python .\.venv-deploy\Scripts\python.exe
```

The worker invokes the existing `python -m app.research_job --url ... --product ... --max-cost-usd 0.05 --image-max-cost-usd 0` for each pair. Azure uses a Key Vault value and an ephemeral local MCP credential instead of your local key file. No model request is made on your deployment computer.

The private `catalog/ledger/YYYY-MM.json` reserves five US cents **before** each attempt. There are at most forty reservations, and each product/store is attempted once. Failures, timeouts, bot blocks, unconfirmed offers and manual retries do not refund reservations or repeat the same pair. A renewable catalog lease serializes jobs, and conditional ledger writes reject stale updates. Re-running CollectMonthly resumes only unattempted pairs. The date window is UTC; a run stops scheduling new pairs at a month boundary. A crash can leave an attempt marked reserved; this is intentional fail-closed behavior. The ledger records the agent's accounted cost when available, not an independently verified provider invoice.

Availability and product identity remain conservative: prices require matching structured evidence; different variants or changed listing URLs are rejected for manual review. A retailer may block automation or lack the product. **Forty searches do not guarantee forty usable offers.** The page averages only accepted, in-stock BRL prices, one latest reading per retailer, and shows the first available date's average as baseline. Monthly readings are visibly dated and usually marked stale after 48 hours. Rejected raw reports remain in private ADLS for review; they are not silently mixed into averages.

## State, secrets and limits of this demo

- Terraform state is initially **local**, separate from the production stack, and git-ignored. Back up `webapp/deploy/portfolio/terraform/terraform.tfstate` securely; losing it loses Terraform's ownership record. No API secret values are stored there. To move it to Azure, use an existing private state account/container, add `backend "azurerm" {}` inside the Terraform block, and run `terraform init -migrate-state` with `use_azuread_auth=true`, the account, container, resource group, and a **new** key such as `preco-claro-portfolio.tfstate`. Never reuse a production state key.
- Key Vault and private data containers use public HTTPS endpoints protected by Entra RBAC. There are **no** paid private endpoints/network isolation. `$web` intentionally serves the public portfolio anonymously even though other containers do not permit public blob access.
- All browser, proxy, MCP and agent processes share one short-lived container/managed identity. Browser and MCP subprocess environments exclude OpenAI credentials and Azure identity variables, and existing SSRF/DNS/redirect controls remain active. This is a cost-saving isolation tradeoff, not the previous production stack's separate security boundaries.
- There is no paid Log Analytics sink. Execution status is available in Container Apps; successful smoke status, raw reports and the budget ledger are private blobs. SDK/raw exception bodies are not printed. A failed bootstrap may require a new Smoke run after fixing package/dependency/network issues.
- The static endpoint has no Flask API, server-side email handling or custom authentication. Search/history/details are client-side; suggestions open the user's email app. It uses Azure's HTTPS website hostname. Storage static hosting cannot set all custom HTTP security headers; a supported meta CSP is included. Custom-domain HTTPS/CDN/WAF is outside this budget profile.
- The existing ACR-based deployment is untouched. If it was already deployed, **it keeps billing independently** until you deliberately retire it using its own Terraform state. This new stack does not migrate/delete old cloud resources.
- No Azure resources or paid research calls were created as part of implementing these files. See `VALIDATION.md` for checks and the outstanding real Azure smoke test.

## Troubleshooting and cleanup

```powershell
terraform -chdir=webapp/deploy/portfolio/terraform output
az containerapp job execution list -g rg-YOURNAME-portfolio -n job-YOURNAME-monthly -o table
az containerapp job execution show -g rg-YOURNAME-portfolio -n job-YOURNAME-monthly --job-execution-name EXECUTION_NAME
```

If quota/region capacity prevents the job starting, select a supported region before deploying; do not add dedicated compute. If your subscription disallows budget creation, fix billing permissions instead of assuming an alert exists. For missing prices inspect the private monthly ledger and corresponding reports. To remove **this** demo, back up the data, disable scheduling, then deliberately execute `terraform -chdir=webapp/deploy/portfolio/terraform destroy`. That deletes its stored data and website; it is not part of the deploy script. Key Vault purge protection retains the deleted vault for its retention period.

Suggested commit: `feat: add low-budget Azure portfolio deployment without ACR`.
