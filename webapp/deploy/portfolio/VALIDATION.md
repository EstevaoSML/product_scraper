# No-ACR portfolio validation — 2026-09-27

## Completed locally

- Identity guard update: 411 Python checks passed. Seven cases cover matching managed state, stale markers, whitespace, different subscription/name, ambiguous resources and remote-backend isolation. CheckSettings also passed against the actual local state using its recorded identity; state contents were unchanged and no Azure calls were made. The current marker matches the existing managed resource group, so it was preserved.

- 2026-09-28 capacity workaround: added PRECO_COMPUTE_LOCATION for only the Container Apps environment/job. All 404 Python checks and five Terraform mock plans passed. The alternate-region plan checks both compute resources move while Storage, Key Vault, identity and resource-group locations remain unchanged; the default preserves existing names/locations. No Azure apply or alternate-region capacity test was performed. Reuse the chosen override in later sessions.

- 2026-09-28: explicit registration added for the six Azure namespaces required by the portfolio setup. Terraform validate and all four mock plans passed; a regression check prevents relying solely on default registration. No live registration, plan or apply was run for this fix. The user reported a partial real deployment; preserve its state when resuming.

- Subscription preflight: four mocked cases cover an accessible subscription, stale CLI cache with ARM rejection, a disabled subscription, and competing Terraform credentials. No Azure writes or deployment retries were performed. This check improves diagnosis; it does not establish why the user's live subscription returned 404.

- Full repository suite after the environment-variable and encoding fixes: **411 passed**, including **45 portfolio checks**. Command: `python -m pytest -q --cov=app --cov-branch --cov-fail-under=80`. Coverage of the existing `app` package, with branches: **92.43%**; this is not the coverage percentage for `webapp.portfolio`.
- Regression checks executed in both Windows PowerShell 5.1 and PowerShell 7 verify BOM-free UTF-8 for generated variables/outputs, Unicode preservation, and automatic repair of an existing BOM before Terraform starts. The affected local variables file was repaired without changing its JSON values; Terraform validate passed. No Azure plan/apply was run for this fix.
- Five additional PowerShell cases verify missing/placeholder/invalid subscription rejection before external tools, environment forwarding and restoration after failure, sanitization of legacy generated settings, and rejection of a different deployment identity. All Azure/Terraform mutations are mocked; this update did not deploy resources.
- The Windows sandbox run used a test-process-only temporary-directory ACL workaround restricted to that run's scratch directory. Production code was not changed to relax filesystem permissions for tests. One existing Starlette/AnyIO deprecation warning remains.
- Terraform 1.14.7 / AzureRM 5.6.0: `fmt -check`, `validate`, and **4 mock-provider plans passed** (foundation, manual no-model first test, monthly scheduling, rejection of an unbuilt package). Provider lock includes signed upstream checksums for Windows amd64 and Linux amd64.
- Security/regression checks cover archive traversal/symlinks, digest mismatch, redirect refusal, invalid artifact identifiers, child environment credential exclusion, strict Chrome version input, impostor retailer hosts, variant mismatch, public image path/hash checks, lease loss, and omission of private evidence from static HTML.
- Collector checks verify forty reservations / USD 2, duplicate suppression after success/failure, fail-closed corrupted ledgers, reservation before agent launch, and the existing agent CLI with $0.05 research / $0 image limits. Successful fake research imports evidence; repeated runs skip reserved pairs.
- PowerShell parses, and its default Validate path was exercised with mock external commands: no Azure access, configuration or package build. Execution-status arguments were checked against installed Azure CLI help.
- Private allowlisted package built successfully with matching Chrome/ChromeDriver 154.0.8037.57 from Google's official distribution (approximately 202 MiB). Artifacts are in git-ignored `.ci-runtime/portfolio`; deployment rebuilds current source and pins that ZIP's SHA-256. No user key files, `.env`, `.git`, Terraform state, outputs or vendor tree are packaged.
- Microsoft's public `playwright/python:v1.63.0-noble` manifest digest was resolved from MCR and pinned in Terraform. No registry was created.
- Real seed catalog exported to static HTML: ten cards, the five existing illustrative images, and local content-addressed assets. Search/history/product data are embedded; no Flask API or `/media` endpoint is required. New products have no fabricated observations.

## Outstanding live validation

`python scripts/container_ci.py` was attempted and **blocked** because Docker Desktop's Linux engine pipe was unavailable. Linux Chrome execution and bootstrap dependency installation inside the MCR image have not been verified locally. Unit tests do not substitute for integration testing.

No Azure plan against a subscription, apply, resource creation, paid model call or retailer scrape was executed during this implementation. Mock plans cannot verify subscription permissions, provider registration, regional quota, RBAC propagation or Azure networking. The first real test is `deploy.ps1 -Action Smoke` after deployment: download/verify package, prepare runtime, launch the browser against `https://example.com/`, seed ADLS and publish the static website. It makes no model calls; Azure usage can be billed.

Enabling scheduling or collection through the script requires a successful Azure Smoke record for the **current package digest** and an enabled Key Vault secret. Check retailer blocking, matching accuracy and the provider bill with CollectOne and its private ledger/reports before relying on monthly collection.

The previous production stack was not deployed, migrated or removed. Its validation history remains in `webapp/VALIDATION.md`.

- 2026-09-28: root `portfolio-deployment.local.json` records allowlisted subscription/name/regions before each apply and its completion status. Existing local values were recovered from local state without querying Azure. The file and temporary writes are Git-ignored. Both READMEs document inspection/restoration before setting environment variables. Full suite: **423 passed**, **92.43% app branch coverage**, one existing deprecation warning. Twelve new cases cover creation/replacement, failed plans and partial applies on PowerShell 5.1/7; identity checks also verify that the record remains unchanged. All cloud commands in checks were mocked; no live deployment was run.

## Root JSON configuration (2026-09-28)

The root `portfolio-deployment.local.json` is now the authoritative six-field input file, passed directly to Terraform plan with `-var-file`. PRECO environment inputs, the identity guard and automatic input-file rewrites have been removed. Earlier entries describing them are historical. The local file was migrated preserving subscription/name/regions and recovering the alert email from existing local state; CheckSettings passed without Azure calls. A placeholder-only example is tracked; real settings remain Git-ignored.

Full suite: **415 passed**, **92.43% app branch coverage**, one pre-existing deprecation warning. Obsolete guard/recording checks were replaced with file configuration checks covering invalid/missing input, unknown secret fields, legacy input sanitization, old state/marker tolerance, file precedence and unchanged configuration after successful/failed plans/applies on PowerShell 5.1 and 7. Cloud calls are mocked; no real Azure deployment was performed.

## Deployment upload dependency fix

Added the missing azure-storage-blob==12.30.2 operator dependency and an import-only check before Terraform/Azure operations. Installed the updated requirements in the existing .venv-deploy; the dependency probe and pip check passed. Full suite: 417 passed, 92.43% app branch coverage, one existing warning. New regression checks verify missing SDKs stop before Terraform and report an installation command without traceback. Container integration was attempted but blocked because Docker Desktop Linux engine was unavailable. No Azure apply or upload was run for this fix.

## Private durable diagnostics

Added best-effort stage/status records to catalog/executions/diagnostics with per-run IDs and 30-day lifecycle expiry. The existing MI authenticates each bounded write; exception messages, subprocess output and secrets are excluded. 424 Python checks passed with 92.43% app coverage; Terraform validate and all five mocked plans passed. Tests cover redaction, path injection, unavailable storage and retention. Mock test rollout defaults now explicitly override local generated deployment controls. Docker integration was attempted and blocked by the unavailable Linux engine. No Azure apply or new Smoke run was executed; the prior failure cause remains unknown. Platform failures before Python/identity/storage readiness cannot be persisted by this mechanism.

## Azure venv_create failure compatibility fix

The reported diagnostic confirms environment creation failed after extraction, but does not contain the exact subprocess stderr. Changed bootstrap to the virtualenv bundled by the versioned Microsoft Playwright Python Noble image, using offline seeding and disabling periodic updates. 426 Python tests passed, including environment command/failure propagation checks; app branch coverage remains 92.43%. Terraform validate passed. Docker integration attempted and blocked by the unavailable Linux engine. No Azure apply or Smoke executed; real runtime confirmation is pending.


## Expanded monthly collection — 2026-09-29

- 456 Python checks passed with 92.43% combined app coverage; one existing dependency warning. The QA runner used the existing Windows sandbox scratch-directory ACL workaround.
- Six Terraform mock plans passed; AzureRM validation and formatting passed. Checks include 150 tasks, monthly schedule, 24-hour execution timeout, and the 20 billing-currency-unit Azure alert.
- Added regression checks for preservation of the original ten IDs/history, three-retailer selection, legacy September reservations, image reuse and 50-image ceiling, durable reservations before image calls, lost-lease behavior, and rejection of backdated collection months in Python and PowerShell.
- Docker integration attempted and blocked because the Docker Desktop Linux engine pipe is absent. No live browser, Azure apply, image generation or retailer research was run during implementation.
- Deploy the new package and run Azure Smoke before the documented September CollectMonthly command. Live model accuracy, retailer availability and billed costs are not established by these local tests.
