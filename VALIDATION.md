# Undetected ChromeDriver migration — 2026-09-19

- Replaced Remote WebDriver with a private UC worker using undetected-chromedriver 3.5.5, version_main=152, supplied matching driver, per-job profiles, and process-group cleanup. API response adds browser_backend.
- Dockerfile.browser inherits the existing pinned Chrome 152 image but runs the worker instead of Grid. Chrome retains its internal-only network and egress proxy. Added worker authentication and a 150-second job timeout; preserved API logs and URL restrictions.
- Updated Compose, both-image CI builds/cleanup, browser dependency auditing, Azure sidecar parameters, and usage instructions. Azure requires a custom browser image built from Dockerfile.browser.
- Validation: 94 tests passed; branch-inclusive application coverage 94.36%. Tests include real UC options construction with mocked browser launch, worker protocol/authentication, timeout cleanup, output redaction, URL policy and PowerShell error logging. Compose configuration validation passed.
- Local pip installation encountered Windows temporary-directory permissions. For QA, verified PyPI artifacts for UC 3.5.5, setuptools 80.9.0 and websockets 15.0.1 were unpacked in the task workspace and combined with the existing QA dependencies. This is not a Docker image build or a fresh dependency audit.
- Container CI was attempted but failed at Docker engine preflight: the dockerDesktopLinuxEngine named pipe was not found. The Docker image build, real UC/Chrome 152 compatibility and live retail scrape remain unverified. Start Docker Desktop and run python scripts/container_ci.py, then build/start the everyday stack as documented.

# Validation

## 2026-09-19 — Windows Docker output decoding

- Fixed CI subprocess output capture to read bytes and decode UTF-8 in the main thread. Docker's accented bind-mount paths no longer depend on Windows cp1252 decoding in a background reader thread.
- Added explicit failures for absent, invalid or unexpectedly shaped Docker inspection JSON; command failures still propagate.
- 63 tests passed, including 12 new output-handling cases; application statement/branch coverage remains 94.30%.
- A real subprocess UTF-8 round-trip passed using the repository's Python 3.13.2 virtual environment with cp1252 locale and UTF-8 mode off.
- The user's saved container report records both successful browser scrapes and the live proxy metadata denial before the old inspection failure. That does not establish the remaining network inspection checks passed.
- Docker engine access is still denied in this task. Full container rerun remains pending in a regular PowerShell terminal; the user's previous report was preserved.

## 2026-09-17 — CI setup

- 51 unit/security, configuration and local socket checks passed on Python 3.12.14 with 94.30% combined statement/branch coverage; CI requires at least 80%.
- Two existing Starlette/httpx/AnyIO deprecation warnings remain; no test failures.
- `docker compose config --quiet` passed with the new CI overrides.
- GitHub Actions YAML, new Python source syntax, and the prepared AI evaluation corpus parsed successfully.
- `scripts/container_ci.py` was attempted and failed at its read-only preflight: permission denied on `npipe:////./pipe/dockerDesktopLinuxEngine`. Docker 29.8.0 / Compose 5.5.1 are installed, but this task cannot access the engine. No container build, startup, or real browser scrape has been verified in this task.
- The permission tool cannot represent/grant that Windows named-pipe path. The runner is ready to execute from a regular PowerShell window; do not weaken Docker's engine access controls to accommodate this task.
- No Git remote is configured. The workflow is saved locally and has not run on GitHub.
- Pinned constraint entries were queried with pip-audit: no known vulnerabilities found. Disk caching was disabled in the local audit process because Windows sandbox temporary-file ACLs stalled cache writes. CI uses the standard tool on Linux with full production dependency resolution; that hosted run remains unverified.
- Corrected Compose's API network attachment: API needs an external DNS-capable network to run URL validation. Chrome remains internal-only. This is supported by Moby's internal-network DNS policy and covered by configuration regression checks; browser runtime validation is still blocked.
- AI/agent evaluation cases are prepared only: no AI runtime exists, so no live model evaluation has run.

## 2026-09-16 — Initial implementation

- 41 checks passed on Python 3.12.14 with the versions in constraints.txt.
- Tests cover API key validation, request/body limits (including streamed bodies), encoded response size, concurrency/cooldown, timeout handling, session cleanup, URL restrictions, mixed/private DNS rejection, IP pinning, upstream proxy authorization, and real local socket proxy denial/relay behavior.
- Two dependency deprecation warnings were emitted by Starlette's httpx/AnyIO test-client integration; no failing checks.
- Compose YAML, ARM template JSON, and example parameters parsed; parameter names and absence of a published Chrome port checked.
- Both PowerShell scripts parsed successfully. setup.ps1 ran, creating a random local API key and an empty upstream proxy file; these files are ignored by Git.
- git diff --check passed (Git reports the repository's normal LF-to-CRLF conversion warning for .gitignore).
- Chrome/ChromeDriver version and tag verified against Selenium's official release documentation. Python base tag verified against Docker's official image manifest source.

Not verified in this environment:

- Docker image build/pull, Compose runtime networking, actual Chrome startup, and end-to-end HTML extraction. Docker was not discoverable on PATH or at the standard Docker Desktop location. Registry HTTP attempts also failed here; no image digest was independently retrieved.
- Americanas compatibility. The implementation uses standard Selenium Remote WebDriver rather than the notebook's undetected-chromedriver, so behavior can differ.
- Azure deployment/ARM server-side validation, cloud identity/RBAC, and Fabric execution. Azure CLI was not discoverable and no cloud deployment was attempted.

Next local check: start Docker Desktop with Linux containers, run `docker compose config --quiet`, `docker compose up --build -d`, then `scripts/smoke.ps1` from the repository. Inspect `outputs/scrape.json`. Test the intended Americanas URL separately after the example page succeeds. Run the documented Azure validation/deployment commands only after local checks pass and the prerequisite cloud resources are configured.
# URL diagnostics and persistent logging — 2026-09-19

- Root cause: Ponto Frio was absent from the default exact-host allowlist. Local .env now includes www.pontofrio.com.br and pontofrio.com.br. Public destination checks remain enabled.
- Added explicit URL error codes, request IDs, rotating API JSONL logs on a named Compose volume, and rotating local smoke-client error logs. Sensitive request data and raw exception text are excluded.
- Verification: 74 unit/security/client tests passed, 94.28% branch-inclusive app coverage. PowerShell parsing and docker compose config --quiet passed.
- Added a live CI assertion that the error log survives API container replacement.
- Live container validation was attempted but blocked before startup: permission denied accessing Docker Desktop's Linux-engine named pipe from this session. No live retailer scrape or container persistence result is claimed. Run python scripts/container_ci.py from a terminal with Docker access, then rebuild/start the everyday stack using docs/USAGE.md.
# Public website mode — 2026-09-20

- API, Compose, local .env and Azure template now default to URL_POLICY=public. Existing ALLOWED_HOSTS settings are ignored in this mode. Optional allowlist mode remains available and rejects empty configuration; unknown modes fail startup.
- Public mode still requires HTTPS on port 443, disallows URL credentials, and validates public DNS/IP destinations. Egress pinning and private/metadata-address restrictions remain enabled for browser connections.
- 113 tests passed; branch-inclusive coverage 94.53%. Added public retailer acceptance, mixed/private DNS rejection, URL abuse cases, and public/private final redirect checks. Compose configuration validation passed.
- Updated container CI to exercise public mode with an intentionally unrelated ALLOWED_HOSTS setting. Live container CI was attempted but blocked at Docker preflight because the Linux-engine named pipe was unavailable. Rebuild/recreate the stack to activate the change; no live Kabum scrape is claimed.
# MCP integration — 2026-09-20

- Reviewed the official published 2026-07-28 specification announcement and Python SDK ASGI/deployment documentation. Added official mcp==2.2.0 and pinned its locally resolved dependencies.
- Added authenticated Streamable HTTP /mcp, advertising scrape_html with input/output schemas and untrusted-HTML guidance. Calls reuse the REST scraping function, semaphore, cooldown, URL validation and browser limits. Tool failures produce sanitized MCP error results and persistent request-ID logs. Host/Origin protection remains enabled.
- Added an official-SDK client example with unique timestamped output files, Portuguese setup documentation, Azure MCP hostname settings, and container CI discovery/auth/private-address/live-render checks.
- 125 tests passed, with 94.90% branch-inclusive app coverage. Includes official SDK HTTP clients for 2026-07-28 and legacy 2025-11-25, header/body consistency, schema rejection, output limits, error privacy, private destinations, and HTML prompt-injection text remaining data. No real LLM behavior is claimed.
- Compose configuration validation passed. pip-audit found no known vulnerabilities in the installed MCP dependency set; report: reports/mcp-dependency-audit.json. This targeted audit is not an OS/browser image scan or a complete audit of all existing application dependencies.
- Container CI was attempted but blocked before startup by permission denied connecting to Docker Desktop's Linux-engine named pipe. Live container/retailer execution remains unverified in this session. Rebuild api and chrome, start the stack, and run scripts/mcp_client.py or scripts/container_ci.py from a Docker-enabled terminal.

## Azure Functions / Terraform validation — 2026-09-20

- 141 Python checks passed; branch-inclusive app coverage 95.47% (Functions adapter 100%).
- Terraform 1.13.5 with AzureRM 5.6.0: fmt, validate and two mocked security plans passed.
- Timestamped Functions source staging completed; no environment/secret files included.
- Container CI attempted: Docker Desktop Linux engine named pipe unavailable. No live browser result claimed.
- No live Azure plan, deployment, remote build or Functions-host integration performed. Follow deploy/terraform/README.md for subscription acceptance checks.


## Agent navigation / Container Apps — 2026-09-20

- 194 tests passed; combined statement/branch coverage 95.31%. One dependency deprecation warning.
- Tests include persistent browser leases, owner/unknown-session rejection, expiry, cleanup, action budgets, stale/retargeted references, restricted search/link navigation, separate backend credentials, MCP dispatch and evidence-backed report validation.
- Provider-neutral agent orchestration was tested with deterministic decisions, not a live LLM. Semantic product matching and model adversarial behavior have not been evaluated against a real model.
- Terraform 1.13.5 / AzureRM 5.6.0 formatting and validation passed; both mocked plans passed. No Functions resources remain in the current Terraform configuration.
- Docker integration attempted after adding real MCP session and synthetic retail DOM checks. It stopped at Docker engine preflight with permission denied on dockerDesktopLinuxEngine. No live Chrome navigation result is claimed.
- CLI argument validation/help checked. No Azure resources deployed or changed; no live Azure plan performed.
- Migration may replace the old environment and remove Function resources if the earlier stack was deployed externally. Review the guide and actual plan before any apply.


## Azure DevOps / Log Analytics — 2026-09-21

- 205 Python tests passed; app statement/branch coverage 95.33%. One dependency deprecation warning.
- AzureRM 5.6.0 and Azure DevOps provider 1.16.0 installed with provider signature verification and lock files.
- Both Terraform roots passed formatting/schema validation. Four mocked plans passed (three application invariants, one delivery-security plan).
- CI/CD YAML was parsed in contract tests; publishing and log-ingestion verification were tested with controlled responses. This is not Azure DevOps server-side YAML compilation or a live cloud run.
- Container CI attempted; Docker Desktop Linux engine pipe was absent. No live Docker or Azure ingestion result is claimed.
- No cloud apply, repository push, Azure DevOps bootstrap or deployment was executed. Real organization/project/repository IDs, state/network configuration and approvers must be supplied before the documented bootstrap.
