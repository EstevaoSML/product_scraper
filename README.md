# Preço Claro — low-budget Azure portfolio

A retail price-history website with monthly research for **10 products across four retailers**: KaBuM, Amazon Brazil, Americanas and Casas Bahia. The website displays the latest available retailer-price average and the earliest recorded date's average.

The default deployment is `webapp/deploy/portfolio`: Azure Storage static website hosting, private ADLS Gen2 data/packages, one Container Apps Consumption Job, managed identity, Key Vault and a budget alert. It uses a public Microsoft runtime image and **does not create ACR**. Scraping, agent execution and website publication run on Azure; your computer packages the application and runs Terraform. OpenAI is the external model provider.

The previous infrastructure instructions are preserved in [REDME.legacy.md](REDME.legacy.md). Use the steps below for this portfolio; no `backend.hcl` or `terraform.tfvars` needs to be copied from the legacy stack.

## 1. Prepare your workstation and Azure login

Use PowerShell with Python 3.12+, Terraform 1.10+ and Azure CLI with Container Apps commands installed. You need an active Azure subscription and permission to create resources, assign roles, register resource providers and create cost budgets. A tenant-only login is insufficient.

Run all commands from the repository root:

```powershell
Set-Location 'C:\Users\estev\OneDrive\Área de Trabalho\work\web_scraping'

az login
az account list --query "[].{Name:name,Subscription:id,State:state}" -o table

# Run once to prepare the deployment Python environment.
python -m venv .venv-deploy
.\.venv-deploy\Scripts\python.exe -m pip install -r webapp/requirements-deploy.txt
```

## 2. Review and edit the local configuration

`portfolio-deployment.local.json` at the repository root is the configuration source. Reuse your existing file; the following command copies the example only when the local file is missing:

```powershell
if (!(Test-Path -LiteralPath '.\portfolio-deployment.local.json')) {
    Copy-Item '.\portfolio-deployment.example.json' '.\portfolio-deployment.local.json'
}
notepad '.\portfolio-deployment.local.json'
```

Fill in all six fields and save as UTF-8:

```json
{
  "subscription_id": "YOUR-AZURE-SUBSCRIPTION-ID",
  "name": "yourname",
  "location": "eastus",
  "compute_location": "",
  "alert_email": "you@example.com",
  "suggestion_email": ""
}
```

- `subscription_id`: the real subscription ID shown by Azure CLI, not a tenant ID.
- `name`: a globally unique 6–16 character name, lowercase letters/digits, starting with a letter.
- `location`: the region for the resource group and data services.
- `compute_location`: optional Container Apps region override, such as `eastus2`; empty uses `location`. Preserve an existing override on subsequent deployments.
- `alert_email`: your address for Azure cost alerts.
- `suggestion_email`: optional address displayed publicly on the website; empty disables suggestions.

No `PRECO_*` environment variables are needed. The script passes this file directly to Terraform plans with `-var-file`. It never overwrites the file. Real settings are Git-ignored; only the placeholder example is tracked. Keep API keys in Key Vault, not in this JSON.

Changing names or regions can replace resources. The former name/identity safeguard is removed; Terraform continues using the existing state.

## 3. Validate and deploy the infrastructure

```powershell
# Check the JSON without Azure calls or file changes.
.\webapp\deploy\portfolio\deploy.ps1 -Action CheckSettings

# Initialize and validate the portfolio Terraform configuration.
.\webapp\deploy\portfolio\deploy.ps1 -Action Validate

# Build/upload the application package and deploy Azure resources.
.\webapp\deploy\portfolio\deploy.ps1 -Action Deploy -Python .\.venv-deploy\Scripts\python.exe
```

**Deploy creates and applies saved Terraform plans.** It runs a foundation apply, uploads the private package, then applies the job configuration. It does not pause for a separate approval between plan and apply. Monthly scheduling remains disabled after Deploy.

Terraform state is local in `webapp/deploy/portfolio/terraform/terraform.tfstate` and Git-ignored. Back it up securely. If deployment fails partway through, fix the reported issue and rerun Deploy with the same state; completed resources can be reconciled by Terraform. Do not apply plans from a previous failed attempt.

## 4. Test the browser and publish the website

```powershell
.\webapp\deploy\portfolio\deploy.ps1 -Action Smoke -Python .\.venv-deploy\Scripts\python.exe
```

Smoke runs the real browser on Azure against `https://example.com`, seeds the catalog and publishes the static website. It makes no OpenAI calls. The command waits for completion and prints the website URL on success. Initial data includes the existing observed products and selected products awaiting evidence; it does not fabricate missing prices.

## 5. Store the OpenAI key and test collection

```powershell
# Enter the key through a hidden prompt; it is stored in Azure Key Vault.
.\webapp\deploy\portfolio\deploy.ps1 -Action SetSecret -Python .\.venv-deploy\Scripts\python.exe

# Test one product/retailer pair, with a maximum research budget of USD 0.05.
.\webapp\deploy\portfolio\deploy.ps1 -Action CollectOne -Python .\.venv-deploy\Scripts\python.exe
```

After checking the result, collect the remaining monthly pairs:

```powershell
.\webapp\deploy\portfolio\deploy.ps1 -Action CollectMonthly -Python .\.venv-deploy\Scripts\python.exe
```

The collector reserves at most USD 2 for forty research attempts per UTC month, including CollectOne. Repeated runs skip previously attempted pairs. Image generation is disabled; existing illustrations are reused. Azure usage is billed separately, and the Azure budget alert does not enforce a spending cap. See the [detailed cost assumptions and limits](webapp/deploy/portfolio/README.md#expected-monthly-cost).

## 6. Enable monthly updates

```powershell
.\webapp\deploy\portfolio\deploy.ps1 -Action EnableSchedule -Python .\.venv-deploy\Scripts\python.exe
```

This requires a successful Smoke run for the current package and an enabled Key Vault secret. The schedule runs on the first day of each month at 09:00 UTC (06:00 Brasília).

To stop future scheduled runs while retaining the website and data:

```powershell
.\webapp\deploy\portfolio\deploy.ps1 -Action DisableSchedule -Python .\.venv-deploy\Scripts\python.exe
```

To publish the stored catalog without a new research run:

```powershell
.\webapp\deploy\portfolio\deploy.ps1 -Action Publish -Python .\.venv-deploy\Scripts\python.exe
```

After a code update, run Deploy and Smoke again before re-enabling the monthly schedule.

## Documentation and development

- [Detailed portfolio infrastructure, costs, troubleshooting and cleanup](webapp/deploy/portfolio/README.md)
- [Validation results and outstanding integration checks](webapp/deploy/portfolio/VALIDATION.md)
- [Web application guide](webapp/README.md)
- [Retail research agent](docs/RETAIL-AGENT.md)
- [MCP setup](docs/MCP.md) and [agent navigation](docs/AGENT-NAVIGATION.md)
- [CI and regression checks](docs/CI.md)
- [Legacy infrastructure, local Docker scraper and API guide](REDME.legacy.md)
