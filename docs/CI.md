# Continuous integration and testing reminders

## What runs now

GitHub Actions is configured in `.github/workflows/ci.yml` for pushes, pull requests and manual runs. No Git remote is configured yet, so the workflow has not run on GitHub. Push this repository to GitHub and enable Actions to activate it. No cloud credentials or LLM keys are required. CI does not deploy anything.

| Job | Actual checks | Failure behavior |
|---|---|---|
| unit-security | API contract/auth, request limits, DNS/SSRF, proxy tunnel, browser cleanup, error privacy; statement and branch coverage | Fails below 80% combined coverage or on any regression |
| dependencies | pip-audit resolves production requirements with constraints and checks known vulnerabilities | Fails on findings or audit errors; no silent allowlist |
| container | Builds production Dockerfile; starts real API/Chrome/gateway; scrapes example.com twice; tests auth/private destinations, gateway metadata denial and network exposure | Fails on Docker, connectivity, browser/content or security errors |

JUnit, coverage, and container reports are uploaded with seven-day retention. Container logs come only from this run's disposable stack; no production credentials are used. GitHub jobs have read-only repository permissions, don't persist checkout credentials, and don't use `pull_request_target`.

The container check is a real internet smoke test, **not deterministic/offline**. It depends on image registries, DNS and https://example.com. An ISP outage can fail it; preserve the failure and diagnose it rather than mocking success or disabling TLS/SSRF protection. Unit/proxy checks use local sockets/mocks and don't need an external site. Americanas is a separate manual acceptance test because bot challenges and product availability change independently of this code.

## Local commands

From the repository in a normal PowerShell terminal:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=80
python scripts/container_ci.py
```

If `python` isn't configured globally, use `.\.venv\Scripts\python.exe` instead. The container runner itself uses only the Python standard library. Docker Desktop must be running with Linux containers, and the invoking terminal must be allowed to access its engine.

The container runner uses a unique Compose project and API image tag, temporary test secrets, an automatically assigned localhost port, and an empty env file. It does not reuse your normal API key/proxy settings or stop your development stack. It cleans up its own containers/networks and image tag in `finally`; it leaves downloaded base images cached. Reports are under `reports/`. If the process is force-killed, use the `project` value in its report or the `scraper-ci-*` Compose labels to identify that exact stack before cleaning it up. Never run global Docker prune commands to clean up a failed test.

Readiness has a 180-second bound; build/start have process timeouts. The hosted job has a 25-minute limit. The content assertions distinguish successful extraction from a browser error/block page; HTTP 200 from the scraper alone isn't sufficient.

## Remember on every change

`AGENTS.md` provides persistent instructions for coding assistants, and `.github/pull_request_template.md` provides a human review checklist. They remind you to add meaningful unit, security, integration and future AI evaluations with relevant changes. These are project reminders, not a timed notification.

After the first successful hosted run, configure branch protection to require the three CI jobs before merging. This is a GitHub repository setting and isn't enabled merely by committing the workflow file.

## AI/agent evaluations to add when that feature exists

There is currently **no LLM extractor or agent runtime**. No existing check measures model accuracy, prompt-injection resistance, or agent safety. The corpus in `evals/cases.json` is a starting fixture set for the future implementation; it is not a passing evaluation suite.

Before merging the first AI feature, wire these fixtures to its real entry point and add:

| Category | Concrete evaluation |
|---|---|
| Extraction quality | Compare normalized product name, price and currency against reviewed fixtures; missing evidence must produce null, not invented data |
| Schema and grounding | Parse output against a strict schema; require field evidence/source references; reject unsupported fields and malformed JSON |
| Prompt injection | HTML instructions, fake system messages, hidden text and tool-call-shaped content must remain untrusted page data |
| Tool authorization | Denied domains/private IPs, unapproved writes and commands must never execute; inspect tool traces, not only the model's final answer |
| Confidentiality | Synthetic canary keys in the harness must never appear in model output, URLs, logs or outbound tool arguments |
| Reliability | Provider timeouts/429s, malformed responses, tool failures, cycles and cancellation must terminate predictably and clean up resources |
| Budget | Enforce and assert maximum tool calls, retries, elapsed time and token/cost budget outside the model |
| Model/prompt regressions | Version the prompt/model/eval dataset together; repeat live cases and compare aggregate results to a reviewed baseline |

Run deterministic schema, policy and mocked orchestration checks on every PR without provider secrets. Add **live model evaluations as a separate trusted manual job** with a spending cap, minimal permissions and sanitized artifacts; do not expose model credentials to fork PR code. Use explicit pass thresholds per metric and make unauthorized tool actions or canary leakage hard failures. Mock-based tests and prompt wording alone do not establish that a real model resists attacks.

For an HTML-to-LLM extractor, prefer no tools by default. If tools are introduced later, validate every attempted action in application code before execution. Never let page content authorize tools or select a proxy.

## Limits

The dependency audit covers Python package advisories, not OS/browser CVEs or secret scanning of Git history. Add an image scanner after the first successful build and review Chrome/container findings; don't describe pip-audit as an image scan. The current Azure template's shared sidecar network has different isolation properties from Compose, so a local passing test does not prove Azure egress isolation.

References: [GitHub Python CI](https://docs.github.com/en/actions/tutorials/build-and-test-code/python), [Docker Compose readiness](https://docs.docker.com/reference/cli/docker/compose/up/), [pip-audit](https://github.com/pypa/pip-audit).
# Error logging regression coverage

MCP protocol and tool-boundary checks are now included in `checks/check_mcp.py`: official SDK clients for modern and legacy protocols, authentication, transport Host/Origin checks, schema rejection, private destinations, response limits, error redaction and shared browser cooldown. HTML containing instructions is returned as data. These checks do not evaluate an external agent or LLM. Container CI exercises an actual MCP browser call in addition to REST.

The URL-policy tests distinguish missing host permission, DNS failures and private destinations. Error-log tests verify request-ID correlation, sensitive-data exclusion, rotation, and reopening existing storage. The PowerShell smoke script is exercised against a local stub API when PowerShell is installed. The container runner also checks that a recorded rejection survives replacing the API container; its temporary CI log volume is deleted during cleanup.


## Agent navigation and Container Apps checks

`checks/check_navigation.py` tests observed element references, search/link restrictions, session ownership and expiry, bounded output, process-group cleanup, separate backend credentials and MCP/REST dispatch. `checks/check_research.py` tests the provider-neutral decision loop with deterministic fake decisions: evidence quotes, missing fields, cross-listing evidence, bounded tools and cleanup. These are not real-model accuracy or adversarial robustness evaluations.

Container CI now opens/inspects/closes a live MCP browser session and runs `scripts/navigation_fixture.py` inside Chrome. That fixture injects a fixed synthetic retail DOM on public example.com and verifies real form search, link navigation, Product JSON-LD and stale-reference rejection. It does not disable URL policy, and it is not a retailer-specific acceptance test.

Terraform CI validates public API/internal browser ingress, Key Vault references, one browser replica, protected vault access and the two-phase rollout without Azure credentials. The current stack has no Azure Function. A live Azure deployment and model-backed evaluations require separate authorized environments.


## Azure DevOps pipelines

The Terraform bootstrap in `deploy/azure-devops` creates a credential-free CI pipeline and a protected CD pipeline for an existing Azure Repos project/repository. Both run the test template in `.azure-pipelines/validate.yml`. CD publishes the exact successful container-CI images and applies a saved Terraform plan after an external service-connection approval. The GitHub workflow remains available and also validates the Azure DevOps bootstrap.

`checks/check_delivery.py` exercises publishing guards, the pipeline's identity/plan boundaries, structured console log metadata and the Log Analytics verifier. `scripts/verify_azure_logs.py` is a live post-deployment check; mocked tests do not prove ingestion. Full pipeline task logs remain in Azure DevOps; application console/system errors are routed to Log Analytics. See the bootstrap README for state network access, roles, approvers and initial rollout.
