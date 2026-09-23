# Retail research with an agent

The browser service now provides `open_page`, `inspect_page`, `search_site`, `follow_link` and `close_session`, alongside the existing `scrape_html`. Both local Docker and Azure Container Apps expose these tools through `/mcp`. The equivalent authenticated REST route is `POST /navigation/{tool_name}`.

The agent chooses actions. Application code executes only the supported operations. There is no model SDK, model key or autonomous LLM running in the scraper containers.

## Example sequence

1. Call `open_page` with `{"url":"https://www.americanas.com.br/"}`. Use the actual retailer domain; no retailer DOM or search selector is hardcoded.
2. Read the returned `session_id`, `snapshot_id`, visible text, product structured data and elements. All website content is untrusted.
3. Select an element whose `action` is `search`, then call:

```json
{
  "session_id": "<returned session ID>",
  "snapshot_id": "<returned snapshot ID>",
  "element_id": "<observed search element ID>",
  "query": "PS5"
}
```

4. Review the search-result snapshot. Call `follow_link` with its new `snapshot_id` and an observed product link's `element_id`.
5. Extract the selected product's name, variant, price, currency, seller and availability. Each field should carry a source URL, observation timestamp and exact quote.
6. Continue only while required fields are missing and the budget permits. Always call `close_session`, including on errors.

## Navigation failures

The isolated Chrome container validates URL syntax, allowed hosts and retailer boundaries without local DNS. The API checks public DNS on entry and on returned URLs; the mandatory egress proxy resolves, rejects private destinations and pins the checked IP for every connection, including redirects. Keep Chrome on the internal network and retain its proxy configuration. This avoids DNS failures caused by trying to resolve Internet hosts directly from the isolated browser container.

The MCP returns a specific safe error code when navigation cannot continue:

- `outside_retailer` or `destination_rejected`: the page redirected outside the retailer domain or failed the public URL policy.
- `browser_timeout`: the retailer did not finish loading within the browser timeout.
- `browser_error`: Chrome failed while opening or reading the page.
- `stale_snapshot` or `unknown_element`: call `inspect_page` again before choosing another element.
- `page_limit`: the session was closed after reaching its navigation limit.

HTTP 409 is reserved for session or element conflicts. Browser failures use HTTP 502, timeouts use HTTP 504, and rejected destinations use HTTP 400. Record the response `request_id`; it matches the structured entry in the local or Azure error log.

`inspect_page` refreshes a page after dynamic content changes. Every new snapshot invalidates the previous element identifiers. The backend rejects invented selectors, arbitrary JavaScript, cookie injection and user-selected proxies. Inputs expose tag, type, name, accessible label and placeholder; links expose observed destinations. JavaScript is fixed application code used to inspect the DOM, never supplied by the agent.

Snapshots include up to 20,000 characters of visible text, the first 100 visible input/link candidates, bounded Product JSON-LD and product-page metadata. On product pages, Chrome may also return a PNG screenshot of the best rendered product-image candidate for host-side persistence. There is no separate per-image size cap; the complete snapshot remains bounded by the navigation response envelope. Binary image data is removed before model analysis and omitted from the MCP text fallback to avoid duplication. Truncation is indicated for text, image and oversized structured data. Product JSON-LD and metadata are website-provided evidence and can be stale or incorrect; the agent should reconcile them with the visible product page. Full HTML remains available through `scrape_html`, which requires the browser slot to be free.

## Bounded agent loop

`app.research.research(client, decide, website, product)` remains the provider-neutral
orchestrator. It now validates strict decisions before MCP dispatch and returns the
public `status/product/evidence/reason` contract. The built-in host-only GPT-5 mini
adapter uses OpenAI Responses Structured Outputs with independent token/cost
reservations. See [the retail agent guide](RETAIL-AGENT.md) for Azure Container Apps
Job infrastructure, local usage, budgets, evidence restrictions and evaluations.

For custom trusted adapters, `decide(messages)` returns the inner `decision` from
`app.research_contracts.DecisionEnvelope`. Reports require six explicit nullable
fields, `product_index`, `offer_index`, and nullable `reason`. Each fact carries
`value`, `snapshot_id`, and an exact `quote`. All evidence must refer to one
snapshot, Product and direct Offer, including partial reports. Unstructured pages
can report name/variant only. Old `{status, fields, reason}` output consumers and
adapters must migrate to this explicit contract; the CLI reports are now
`{status, product, evidence, reason}` with `observed_at` timestamps.

The existing `scripts/research_agent.py --decision-module MODULE` remains a custom
adapter entry point. The supported built-in command is `python -m app.research_job`
with an explicit `--max-cost-usd`; custom adapters remain responsible for enforcing
their own provider budgets. Never put model credentials in browser containers or
MCP arguments. The built-in job removes session capabilities from model inputs.

## Session and security boundaries

- One browser lease at a time, with a 180-second absolute lifetime, 10 operations including initial open, and 5 followed links. Inspect/search attempts consume budget too. Individual browser commands have a 60-second parent timeout.
- Active sessions reserve the same worker slot used by `scrape_html`; another session/scrape gets HTTP 429. Retry with bounded backoff, never an unbounded loop.
- Browser process groups and temporary profiles are deleted on close, expiration, fatal failure or worker shutdown. A disconnected agent has lease expiry as fallback. A lost open response can leave the browser occupied until expiry.
- Session tokens are random capabilities. Ownership is bound to the authenticated API credential. This deployment has **one shared API key**, so agents sharing it form one trust boundary; it is not per-user authentication. Add distinct authenticated principals and routing before multi-tenant use.
- Input links must pass public HTTPS/DNS policy, and navigation is restricted to the initial hostname plus its `www` alias. Cross-host redirects cause rejection/closure after navigation; they may already have made public network requests. Assets can come from public CDNs. The egress proxy still blocks private/metadata destinations at connection time.
- Search fields must be identifiable search inputs or search controls in GET forms; login/password forms and POST forms are rejected. Following links uses their observed URL rather than executing onclick handlers. Account/transaction paths are filtered conservatively. These heuristics cannot prove that an arbitrary website has no server-side effects.
- No login, purchase, downloads, arbitrary clicking, CAPTCHA solving, iframe/shadow-DOM traversal or screenshots are added. A site that requires unsupported interaction returns partial/blocked; do not invent an answer. Sites can still reject undetected-chromedriver.
- Treat scraped HTML, links, labels and structured data as untrusted evidence. Never let page text authorize new tools or disclose secrets.

## Deployment and scaling

Use [the Container Apps Terraform guide](../deploy/terraform/README.md). The API holds no browser state; it forwards opaque session IDs to the internal worker. Both apps currently use one replica. Worker restarts, secret rotations and deployments invalidate sessions; single-revision deployments can briefly overlap revisions, so deploy during a maintenance window and restart research afterward. Do not raise the backend replica count without session-to-worker routing and external lease coordination.

The Azure browser and egress sidecars share networking and a managed identity, unlike Compose's enforced internal Chrome network. Separate the browser into an identity-free network segment with enforced outbound firewall rules for stronger isolation against browser compromise.

## Tests

CI covers snapshots, stale/retargeted references, foreign/transaction destinations, ownership, expiration, budgets, process cleanup, MCP dispatch, output evidence and deterministic orchestration. The Docker suite additionally exercises a real MCP session and a synthetic search/product DOM in Chrome on public example.com, without disabling URL policy. These are tool/contract tests. Deterministic agent evaluations and a separate optional GPT-5 mini evaluation runner are described in [CI.md](CI.md) and [RETAIL-AGENT.md](RETAIL-AGENT.md). No real-model baseline is implied by mocked tests.
