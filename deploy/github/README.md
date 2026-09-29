# GitHub Actions → low-budget Azure portfolio

GitHub is now the supported CI/CD path. Azure DevOps files remain as legacy references and for removing any previously deployed CI identities. No Azure Repos, ACR, PAT, client secret, or GitHub self-hosted runner is required.

## How it works

1. Pushes and pull requests run `.github/workflows/ci.yml` without Azure credentials.
2. Run **Portfolio deployment** manually from the repository's default branch. It runs that same CI suite before deployment.
3. The protected GitHub environment `portfolio-production` issues an OIDC token. Azure trusts only this repository/environment and exchanges it for a short-lived login.
4. Terraform runs on the GitHub-hosted runner and reconciles Azure resources using the **existing portfolio state**, stored in a private Azure blob with lease locking.
5. The runner builds and uploads a SHA-256 package. The Azure job downloads it, runs browser smoke validation, and publishes the website. Research still runs in Azure; CI does not call OpenAI.
6. Scheduling pauses during release and resumes only after a successful smoke/publication, if it was enabled before the release. On failure it stays disabled; investigate before manually enabling it again.

Terraform does not push code to GitHub or deploy itself. GitHub Actions invokes Terraform. The separate Terraform stack in this folder creates the Azure identity, federated trust, permissions and state container. The portfolio stack manages the app resources.

This CD path updates an already deployed portfolio. First deployment, IAM changes and destructive/replacement infrastructure changes remain explicit local Terraform operations. The pipeline rejects plans with deletions or role-assignment changes and has no RBAC administrator role. The runner has Contributor on the portfolio resource group and Blob Data Contributor on its storage account (including catalog, packages, website and state), but no Key Vault secret role. The original human operator assignments are read from state and preserved.

## 1. Bootstrap the GitHub connection once

Keep your root `portfolio-deployment.local.json` unchanged. It remains the configuration source. First complete the root README's initial portfolio deployment using your local Azure login. Do not use a legacy stack's state.

From the repository root, in PowerShell:

```powershell
az login
Copy-Item deploy/github/github.tfvars.example deploy/github/github.tfvars
notepad deploy/github/github.tfvars
# Set github_repository to the exact owner/repository (case matters).
# Current repository: EstevaoSML/product_scraper

terraform -chdir=deploy/github init
terraform -chdir=deploy/github fmt -check
terraform -chdir=deploy/github validate
terraform -chdir=deploy/github plan -out=github.tfplan
# Review the plan, then:
terraform -chdir=deploy/github apply github.tfplan
terraform -chdir=deploy/github output
```

Requires an operator allowed to assign roles and create the identity resource group, plus existing Blob Data Contributor access. Preserve a private backup of `deploy/github/terraform.tfstate`: this is a separate bootstrap state, never the application state. Neither state is committed.

Bootstrap adds only a resource group, managed identity, federated credential, two role assignments and a private `github-tfstate` container in your existing storage account. It adds no ACR, VM or Log Analytics. Storage state operations and GitHub Actions usage can incur usage charges; they are not a guaranteed fixed $20 cap.

## 2. Migrate the EXISTING app state (do not skip)

Stop local Terraform operations while migrating. Back up the app state privately first. Do not delete or replace it with an empty state. If you already use a remote backend, back up that state and migrate from that backend instead of the local copy below.

```powershell
$config = Get-Content portfolio-deployment.local.json -Raw | ConvertFrom-Json
$infra = 'webapp/deploy/portfolio/terraform'
Copy-Item "$infra/terraform.tfstate" "$infra/terraform.tfstate.before-github"
Copy-Item "$infra/backend.tf.example" "$infra/backend.generated.tf"
$backend = @"
storage_account_name = "st$($config.name)"
container_name = "github-tfstate"
key = "portfolio.tfstate"
use_azuread_auth = true
use_cli = true
"@
[IO.File]::WriteAllText((Join-Path (Resolve-Path $infra) 'backend.hcl'), $backend, (New-Object Text.UTF8Encoding($false)))
terraform "-chdir=$infra" init -migrate-state -backend-config=backend.hcl
# Confirm the state-copy prompt only after checking the destination.
terraform "-chdir=$infra" state list
terraform "-chdir=$infra" output
```

Check that the existing job, storage, vault and role assignments are present. The pipeline refuses to start if the remote blob is absent or belongs to a different portfolio. Do not copy the legacy `deploy/terraform` state. Keep this generated backend file locally so subsequent local deploy commands use the same remote state. New local checkouts must repeat backend configuration with `init -reconfigure` pointing at this existing blob, NOT initialize a new local state.

The state container has Terraform `prevent_destroy` in the bootstrap stack; it is not a backup policy. Keep private state backups. Application identities only have access to their runtime containers, not the state container.

## 3. Configure GitHub

In repository **Settings → Environments**, create **portfolio-production**:
- Restrict deployment branches to your default branch (normally `main`).
- Configure required reviewers if available for your GitHub plan.
- Add these **environment secrets**:
  - `AZURE_CLIENT_ID`: bootstrap Terraform output `client_id`.
  - `AZURE_TENANT_ID`: bootstrap output `tenant_id`.
  - `PORTFOLIO_CONFIG_JSON`: the complete contents of your root `portfolio-deployment.local.json`.

The workflow materializes that same JSON file on the ephemeral runner. Do not put these values in tracked YAML/tfvars. When editing local configuration, update its GitHub secret copy before releasing. The OpenAI key stays in Azure Key Vault and is never copied to GitHub.

Protect the default branch, require **Scraper CI** checks, and enable GitHub Actions. Push the reviewed changes to the GitHub remote. Then select **Actions → Portfolio deployment → Run workflow**, choosing the default branch. Inspect the run and website. No remote push or Azure apply was performed by the implementation assistant.

Before any subsequent local Deploy/EnableSchedule/DisableSchedule operation, refresh the ignored rollout controls from the shared state. Otherwise an old local package digest could undo a GitHub release:

```powershell
python webapp/deploy/portfolio/github_release.py --sync-local
# Then invoke the desired deploy.ps1 action with your deployment Python.
```

If smoke fails, the workflow fails and leaves scheduling off. Read the printed private diagnostic blob, fix the error, rerun deployment, then explicitly re-enable scheduling locally after validation. A failed run does not automatically roll back an applied package or undo a published website.

## 4. Retire Azure DevOps safely

No local Azure DevOps Terraform state was found during implementation; actual Azure deployment was not verified. Names below are derived from `deploy/azure-devops/main.tf`. Delete them **only if they exist and are dedicated to the retired pipelines**, after GitHub deployment works.

| Old resource | Action |
| --- | --- |
| `id-<legacy-app-name>-plan`, `id-<legacy-app-name>-release` | Remove their role assignments, federation and identities through the old bootstrap Terraform stack. |
| Federated credentials `azure-devops-plan`, `azure-devops` | Remove with the old identities. |
| Custom role `<legacy-app-name>-terraform-refresh` and its assignment | Remove if unused elsewhere. |
| Old assignments: Reader, Contributor, AcrPush, Blob Data Reader/Contributor, conditional RBAC Administrator | Remove only assignments whose principal is one of the two old CI identities. Keep runtime/operator assignments. |
| Azure DevOps CI/CD pipelines, service connections, checks, production environment and repository branch policies | Remove/disable in Azure DevOps; these are not Azure ARM app resources. Keep the repository until code/history migration is confirmed. |
| Old identity resource group | Remove only if empty and exclusively used for retired identities. The old Terraform stack references it as data and will not delete it. |

**Keep** your portfolio resource group, storage/ADLS and all historical data, website, Key Vault, runtime job identity, Container Apps environment/job, budget and their permissions. Keep both Terraform state stores. Moving source control does not require deleting/recreating the app.

If the old bootstrap was applied and you have its original state/configuration and Azure DevOps authentication:

```powershell
# Disable the old pipelines first. Run only against their ORIGINAL bootstrap state.
terraform -chdir=deploy/azure-devops state list
terraform -chdir=deploy/azure-devops plan -destroy -out=retire-devops.tfplan
# Review: only DevOps CI objects, its two identities and their roles may be removed.
# Stop if you selected an application stack/state. Apply only after that review:
terraform -chdir=deploy/azure-devops apply retire-devops.tfplan
```

Do not run destroy in `deploy/terraform` or `webapp/deploy/portfolio/terraform`. Without the original DevOps state, inventory the two identity principals and their assignments in Azure before removing individual resources; do not reconstruct state or delete entire groups blindly. Old ACR/always-on app resources are a separate legacy runtime migration, not required by the GitHub switch.

References: [GitHub OIDC with Azure](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-azure), [Terraform Azure backend](https://developer.hashicorp.com/terraform/language/backend/azurerm).


For 50 products, reusable images and the September manual trigger, see [monthly collection](../../webapp/deploy/portfolio/MONTHLY.md).
