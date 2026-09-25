# GPT-5 mini retail research on Azure

The agent is a Python process in its own **Azure Container Apps Job**. It calls
the existing `/mcp` and the OpenAI Responses API. One researcher performs navigation
and extraction; application code authorizes every action. The browser does not
receive model credentials or install the agent's dependencies.

## Infrastructure and rollout

`enable_research_agent=true` adds an agent identity, private StorageV2 account and
`research` container, Blob private endpoint/DNS, scoped RBAC and (when
`deploy_workloads=true`) a manual Container Apps Job. Existing ACR, Container Apps
environment, Key Vault and Log Analytics are reused. No new public API or queue.

The job has one replica per execution, no automatic platform retry, a 300-second
platform timeout and a 270-second process deadline. A bare start executes `--help`
and does not make paid calls. A Blob lease serializes overlapping job executions.

1. Review Terraform before applying. On an existing scraper, retain
   `deploy_workloads=true`; turning it off removes existing workloads. Prepare
   the agent image and secret before enabling its deployment in that stack.
2. A trusted operator stores `openai-api-key` in the existing Key Vault using a
   secure operator session. Never put values in tfvars, Terraform secret data
   sources, build arguments, command arguments or source control.
3. Build `Dockerfile.agent` into the existing registry as `retail-agent:TAG`; set
   `agent_image_tag=TAG`. API/browser images need no model dependencies.
4. Review and apply the saved plan. The agent can read only `openai-api-key` and
   `mcp-api-key`, not the browser backend key. Existing identities receive no
   additional model-secret permissions.
5. Start a manual job with URL, product and an explicit USD cap. Only trusted
   operators should have job-start permissions, since starting jobs exercises
   their configured identity and secrets.
6. Read `reports/<run-id>.json` in the private `research` container. Operators
   need explicit Blob Data Reader access and VNet/private-DNS connectivity.
   Log Analytics records safe metadata, never page bodies or prompts.

This Terraform does not deploy itself. Mock tests are not live Azure acceptance.
The browser/egress isolation limitations in the existing deployment guide remain.

## Usage

Install `requirements-agent.txt` on the agent host. Supply `OPENAI_API_KEY`
securely through its process environment. The scraper key is separate:

```powershell
python -m app.research_job --url 'https://www.example-retailer.com/' --product 'Console Nova Digital 1TB' --max-cost-usd 0.05 --key-file '.\secrets\api_key.txt'
```

The example URL is a placeholder. Local reports and diagnostics go to `outputs/agent/`
by default (override with `--output-dir`). Filenames include the run start timestamp
in UTC and a unique UUID: `research-YYYYMMDDTHHMMSSffffffZ-<run_id>.json`
and `diagnostics-YYYYMMDDTHHMMSSffffffZ-<run_id>.json`. The report and diagnostics
share the same timestamp and UUID, so earlier reports are preserved. Azure reports
also use timestamped filenames. Azure uses
`MCP_API_KEY`, `MCP_ENDPOINT`, `AZURE_CLIENT_ID` and `RESEARCH_STORAGE_ACCOUNT`,
wired by Terraform. The OpenAI endpoint/model are fixed in trusted code.

### Optional product illustration

The host-only `ProductImageTool.generate_product_image(ProductImageRequest)` in
`app/research_images.py` uses **`gpt-image-2.5-flare`** via the Image API. It reuses
the same `OPENAI_API_KEY` already loaded for GPT-5 mini. No new secret, MCP tool,
browser permission, package or Terraform resource is required. The scraper never
receives this key. The request contract permits only `product`; credentials,
output paths, endpoints, quality and count are controlled by application code.

Enable one image per search invocation with a separate explicit image budget:

```powershell
python -m app.research_job --url 'https://www.americanas.com.br/' --product 'PS5 Digital Edition' --max-cost-usd 0.05 --image-max-cost-usd 0.05 --key-file '.\secrets\api_key.txt'
```

Without `--image-max-cost-usd` (default zero), generation is disabled. Images are
generated after browser cleanup from the user's original search label, even if
the research result is partial; a blocked research result skips generation.
This is a synthetic illustration, not the retailer's photo or evidence of an
exact variant, price, seller or availability. It never fills research fields.

Local PNG and metadata files use `outputs/agent/images/product-<timestamp>-<run_id>`
with `.png` and `.json` extensions. Azure uses `images/` inside the existing private
`research` Blob container. The diagnostic record contains an `image` result,
including failure/skip reason, accounting and artifact path. An HTTP image failure
does not replace the saved research report. Add the same image budget argument
when starting an Azure job; rebuild the agent image to include the new module.

Limits are enforced outside the model: one request, no retries (including 429),
60 seconds total, `n=1`, `1024x1024`, `quality=low`, PNG, bounded response bytes,
no external image downloads. Input accounting uses prompt UTF-8 bytes plus a
margin; preflight reserves 1,024 image output tokens at $30/million and text input
at $5/million. These are conservative local estimates, **not a provider-enforced
dollar cap**. Provider usage updates accounting when available; unknown usage
retains the reservation. Unexpected actual spend above the image allowance is
reported as `budget_exceeded`; the tool cannot undo an already billed request.
The image allowance is separate from `--max-cost-usd` for research.

Documentation checked 2026-09-23:
[Flare model](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare),
[Image API generation and usage](https://developers.openai.com/api/docs/guides/image-generation).
Account access/organization verification may still be required. Unit tests use
synthetic responses and do not establish live model access or visual fidelity.

### Azure invocation

```powershell
az containerapp job start --name YOUR_RESEARCH_JOB --resource-group YOUR_RESOURCE_GROUP --container-name agent --args '--url=https://www.example-retailer.com/' '--product=Console Nova Digital 1TB' '--max-cost-usd=0.05'
```

Check `az containerapp job start --help` for your CLI version. Only task parameters
are passed; the container environment keeps Key Vault references.

## Contracts and controls

`app.research.research(client, decide, website, product)` remains provider-neutral.
`decide(messages)` returns one strict search/link/inspect/final decision from
`app.research_contracts.DecisionEnvelope`. The OpenAI adapter wraps it in
`{"decision": ...}` for Structured Outputs, then unwraps it for the loop.

Session capabilities are omitted from model observations; the host injects them
when dispatching MCP. Searches use the exact user query. Elements must exist in
the latest snapshot and have the correct action. Foreign/transaction links are
rejected locally, and the scraper retains DNS/SSRF/egress checks. Repeated
identical searches/links are rejected. Blocked pages stop before another decision.

| Budget | Enforced outside the model |
|---|---|
| Session | 180 seconds, reserving the final 10 seconds for cleanup |
| Navigation | 10 attempts including open; 5 followed-link attempts |
| Model | 8 calls; 32,000 cumulative input and 8,000 cumulative output tokens |
| Per model call | 2,048 output tokens including reasoning; 45 seconds |
| Invalid decisions/reports | Up to 2 corrective retries |
| MCP 429 | Up to 2 retries honoring delay and remaining time |
| Provider 429 | Up to 2 retries, each charged to model budgets |
| Cost | Explicit positive `--max-cost-usd`, maximum USD 10 |

The adapter reserves conservative input tokens (UTF-8 request bytes plus framing
margin) and maximum output before dispatch, then reconciles actual usage.
Unknown usage retains the full reservation. This can stop early. Rates are
standard text input USD 0.25/million and output USD 2.00/million, checked 2026-09-21
against [GPT-5 mini documentation](https://developers.openai.com/api/docs/models/gpt-5-mini).
Reverify rates before deploying: local accounting is not a provider billing lock.
The selected model remains `gpt-5-mini`; prompt version is `retail-research-v2`.

A 60-second Blob lease is renewed every 20 seconds, acquired before any paid/model
or MCP call. A busy lease fails fast. Lost renewal cancels work and waits for its
`finally` before release. The scraper lease also protects against other clients
that do not participate in this lock.

Cleanup attempts `close_session` in `finally`, including errors/cancellation,
with its own timeout. A lost open response may leave no known session ID, and a
force-killed process cannot run finally: server expiry remains the fallback.
Failed cleanup is recorded as `cleanup=failed`, never reported as successful.

## Evidence and output

The public report has `status`, `product`, `evidence` and nullable `reason`.
Product fields are name, variant, price, currency, seller and availability, with
literal string values or null. Prices are not silently converted or normalized.
Retail pages may display list, installment and Pix/cash prices together. The
selected direct `Offer` remains canonical for financial fields; those display
alternatives alone do not make the result contradictory.
The agent selects the Product and direct Offer. The application then copies each
available financial scalar from that Offer. A missing seller therefore leaves
only `seller` null while retaining price, currency and availability when present.
Every non-null value has an exact quote, source_url and observed_at.

Internal reports also select zero-based `product_index` and `offer_index` from
observed Product JSON-LD, and each fact contains snapshot_id. All facts must refer
to one snapshot, one Product and one direct Offer, even in partial results.
The selected name must appear in visible text. Name/variant must support the query
tokens. Price, currency, seller and availability must equal the selected Offer's
corresponding values; AggregateOffer and cross-seller combinations are rejected.

Without structured Offer association, only name/variant can be returned, with
financial fields null and status partial. This conservative limitation sacrifices
recall to avoid associating unrelated cards on a shared URL. JSON-LD can still be
stale or dishonest. Source association is not a guarantee of retailer truth;
semantic variant/accessory matching and contradictions need model evaluation.

When no structured Product is selected and the identity quotes contain only words
from the query, the validator clears all facts and evidence and returns partial.
Echoed search terms do not establish a product listing. This conservative check
can also reject a genuine unstructured title identical to the query; additional
product-specific evidence is required. A structured Product with that same title
continues through the normal identity and offer checks.

## Tests and evaluations

### Diagnosing partial runs

Local runs also save `outputs/agent/diagnostics-<timestamp>-<run_id>.json`, containing the same
safe counters printed to the console. Exceptions produce an allowlisted code
in `reason` and `navigation.failure`, with the failing stage and HTTP status
when available. Raw exception messages, provider bodies and credentials are
never included in these diagnostics.

`navigation.rejections` records an allowlisted validation code and decision number
for each rejected decision. The next retry receives a specific corrective hint,
and an exhausted invalid-decision limit includes the last code in `reason`.
No raw model output or exception text is logged. `requested_identity_mismatch`
means a distinguishing query term was absent from the observed identity. The
validator normalizes accents, `PS5`/`PlayStation 5`, and `Digital Edition`/`Edição
Digital`; model, capacity, bundle and other variant terms must still be supported.
Seller and evidence checks are not relaxed by retry feedback.

Every completed local agent run saves a compact result at
`outputs/scrapes/scrape-<timestamp>-<run_id>.json`. The file is an ordered array
with one object for every page assessed by the Agent. Each object contains only
`url`, `has_ps5_info` and `ps5_info`; the nested object contains `product_name`
and `description`, with missing values represented as null. The Agent decides
these values from the current observation. Product images and image URLs are not
collected, sent to the Agent or stored.

The host never saves session capabilities and records the JSON path as
`scrape_output` in diagnostics. Search refinements
and pagination links are hidden from the model after search so it must choose an
observed product result. Treat saved descriptions as untrusted website data. Azure
jobs store the final report under `reports/` and the assessment array under
`scrapes/` in private Blob Storage.

`model_input_budget` means the conservative input estimate exceeded the remaining
token allowance before dispatch. Zero model calls with one navigation operation
can indicate this preflight stop; it does not establish an invalid API key.
Increasing the USD cap does not increase the input token limit.
Every snapshot is filtered before dispatch, using the trusted navigation stage:
homepages expose search fields; results expose search fields and query-matching
links; product pages prioritize every product's identity and complete offers,
without navigation menus. Product/offer indexes remain unchanged.
Other unrelated product attributes are omitted. Relevant visible lines and their
neighbors retain variant, seller, price and contradictory evidence as exact excerpts.
Navigation views are capped at 4,000/7,000 serialized bytes and leave 8,000 input
budget units for extraction; the request schema/instructions are also accounted.
`product_evidence_budget` stops extraction before dispatch if essential product
records or selected visible evidence cannot fit, instead of sending empty products.
The original snapshot remains the evidence validator's source. Filtering can reduce
recall and does not establish semantic correctness. After filtering,
`not_found` is downgraded to `partial`; omitted evidence does not establish absence.
If even the mandatory context cannot fit, `model_input_budget` still stops dispatch.
No token/cost cap is raised and no extra model request is used for compaction.
`provider_authentication` means HTTP 401; `provider_quota` means a billing or
quota rejection and is not retried. `provider_rate_limit` means bounded rate-limit
retries were exhausted. `provider_connection` and `provider_timeout` identify
transport failures. Share the safe code/counters when troubleshooting, never keys.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=app --cov-branch --cov-fail-under=80
python scripts/evaluate_research.py
python scripts/container_ci.py
python scripts/agent_container_ci.py
```

`evals/retail_fixtures.py` is a versioned synthetic navigation corpus. Offline tests
exercise the production loop and adapter through fake MCP/HTTP transports: exact
extraction, variants, multiple sellers, quotes, injection, forbidden calls, canary
secrets, budgets, retries, blocks, malformed output, cancellation and cleanup.
Container CI remains a real Docker/example.com check using a synthetic retail DOM.

Manual model evaluation (paid, synthetic pages only):

```powershell
python scripts/evaluate_research.py --live-model --max-cost-usd 0.50 --repeats 3
```

The manual GitHub workflow `agent-evaluation.yml` runs only from the default branch
with an explicit total cap. Configure environment `retail-model-evaluation` with
required reviewers, branch restrictions and its `OPENAI_API_KEY` secret first.
Only sanitized metrics are uploaded. PR jobs never receive provider secrets.
No passing real-model baseline is claimed until that job is run and reviewed.
See `evals/AGENT-EVALUATION.md` for thresholds. Real-retailer acceptance is optional
and manual, separate from the synthetic evaluation suite.
