# Monthly collection: 50 products and September 2026

The existing Azure Container Apps Job runs one browser sequentially over 50 fixed products × KaBuM, Amazon Brazil and Americanas: 150 research attempts per UTC month. The original ten product IDs and all recorded observations/images are retained. The 40 additions are search targets, not verified listings or promises of availability.

## Deploy the changes first

From the repository root, using the existing configuration file and Terraform state:

- **GitHub path:** commit/push, then run **Actions → Portfolio deployment** on the default branch. Wait for its Azure smoke/publication step to succeed. This deploys the package and new Terraform timeout/limits, and does not run paid research.
- **Local path:** if GitHub has already deployed against shared state, refresh the local rollout controls first. Then Deploy and Smoke:

```powershell
# Only after the shared-state migration, with Terraform on PATH:
python webapp/deploy/portfolio/github_release.py --sync-local

# Local deployment alternative to GitHub:
.\webapp\deploy\portfolio\deploy.ps1 -Action Deploy -Python .\.venv-deploy\Scripts\python.exe
.\webapp\deploy\portfolio\deploy.ps1 -Action Smoke -Python .\.venv-deploy\Scripts\python.exe
```

Do not run both deployment paths concurrently. A local Deploy leaves scheduling disabled. GitHub restores the prior schedule only after successful smoke/publication. Smoke publishes the expanded 50-product catalog, but does not invent prices or generate new images.

If the OpenAI secret has not been provisioned, run `deploy.ps1 -Action SetSecret -Python .\.venv-deploy\Scripts\python.exe` once. It prompts without echo and stores the value in Key Vault.

## Run September now

**Run while the UTC month is still September 2026**, preferably September 29 to leave time before the month boundary. All observations carry their real collection timestamps; this does not retrieve prices from September 1 or backfill historical prices.

After the new deployment and smoke pass, in PowerShell:

```powershell
# Refresh after a GitHub deployment, if using the shared remote state:
python webapp/deploy/portfolio/github_release.py --sync-local

# Start the Azure job now; the agent runs in Azure, not on your PC:
.\webapp\deploy\portfolio\deploy.ps1 -Action CollectMonthly -Month '2026-09' -Python .\.venv-deploy\Scripts\python.exe
```

This command verifies the smoke marker belongs to the deployed package and that the Key Vault secret exists, starts an Azure execution, prints its name and private diagnostic blob, then waits. It can take hours (24-hour execution limit). Closing your terminal does not cancel Azure's execution; inspect the existing execution before starting another.

`-Month` rejects any month other than the current UTC month both locally and in the worker. After September ends, these commands cannot produce genuine September observations. A run crossing the month boundary stops scheduling new requests; an already-running request retains its actual timestamp.

On success, open the website URL printed by the script. Not every retailer necessarily yields a verified price: bot challenges, unavailable variants and extraction failures remain explicit, never replaced with fabricated data.

## Enable automatic future months

After the manual run finishes:

```powershell
.\webapp\deploy\portfolio\deploy.ps1 -Action EnableSchedule -Python .\.venv-deploy\Scripts\python.exe
```

Azure then starts the job at `0 9 1 * *`: the first day of each month at 09:00 UTC, currently 06:00 São Paulo. No recurring GitHub workflow is needed. The next scheduled run after the September manual collection is October 1; it is a separate month's budget.

## Recovery, images and budget

- `catalog/ledger/2026-09.json` is private. Its `attempts` records product/retailer pairs; `images` records per-product image requests. Reservations are saved before paid calls.
- Rerunning CollectMonthly in the same month skips every reserved pair, including failed or interrupted attempts, and continues with unattempted pairs. It does **not** automatically retry failures or refund uncertain charges. Thus a successful execution means the batch finished, not that all 150 prices were verified.
- Missing images are generated independently using the agent's existing image tool, once per product rather than once per retailer. Existing image blobs are reused across months. Successful images are persisted immediately to ADLS; image failures may be tried in a later month if still missing. A crash after payment but before persistence may require a later month's attempt.
- The new package refreshes the stored manifest without deleting history. Existing Casas Bahia observations remain historical data, but that retailer is no longer queried.
- The existing schema-v1 ledger is retained. Up to ten already-reserved Casas Bahia entries are accepted alongside 150 active-retailer attempts, adding at most $0.50 of legacy reservation to September. This avoids discarding earlier charges or blocking the new catalog.
- Normal reservations: $7.50 research + at most $2.50 images. Existing five images reduce the initial missing-image count to 45 if their blobs remain available. Later months normally need only research calls.
- The $20 total is a planning target. Azure budget alerts are set to **20 subscription billing-currency units**, cover Azure only and do not stop spending. Model estimates are not a server-side billing cap. An image usage overrun fails the run and blocks further paid collection in that month's ledger pending review.
- Inspect the printed diagnostic blob for bootstrap/runtime failure and the private ledger for per-product outcomes. Do not delete ledgers or Terraform state to force retries. No real searches/images were executed as part of implementing this change.

