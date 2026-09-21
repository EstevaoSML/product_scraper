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

Snapshots include up to 20,000 characters of visible text, the first 100 visible input/link candidates and bounded Product JSON-LD. Truncation is indicated for text/oversized structured data. Product JSON-LD is website-provided evidence and can be stale or incorrect; the agent should reconcile it with the visible product page. Full HTML remains available through `scrape_html`, which requires the browser slot to be free.

## Bounded agent loop

`app.research.research(client, decide, website, product)` supplies an optional provider-neutral orchestration loop. `client` is a connected official MCP client. Implement a **trusted local** async callback `decide(messages)` using your chosen LLM/agent framework. It receives system instructions, the task and untrusted tool observations; return a JSON dictionary such as:

```json
{"tool":"search_site","arguments":{"snapshot_id":"<observed>","element_id":"e0","query":"PS5"}}
```

Or return a final report:

```json
{
  "report": {
    "status": "partial",
    "fields": {
      "name": {"value":"PS5","snapshot_id":"<observed>","quote":"Sony PS5 Digital Edition"},
      "variant": {"value":"Digital Edition","snapshot_id":"<observed>","quote":"Sony PS5 Digital Edition"},
      "price": null,
      "currency": null,
      "seller": null,
      "availability": null
    },
    "reason": "Price and seller are not shown."
  }
}
```

`complete` requires all six fields, evidenced from the same product-page URL. Every non-null field must quote an observed snapshot and contain its stated value; source URL and timestamp are filled from that snapshot. This prevents unsupported values from passing the output contract, but does not prove that an LLM matched the correct variant or associated the right seller with a price. Those remain model evaluation requirements. Return `partial`, `not_found` or `blocked` when appropriate.

The decision messages are a provider-neutral representation, not a drop-in request for every model SDK. Your adapter must translate roles/tool observations to its provider's message format while retaining the untrusted-data boundary. Enforce model token/cost limits and cancellation inside the adapter. Do not expose additional tools to the model during this loop.

After creating a module such as `my_agent.py` in the repo root with `async def decide(messages)`, run:

```powershell
python -m pip install -r requirements.txt
python scripts/research_agent.py --url 'https://www.americanas.com.br/' --product 'PS5 Digital Edition' --key-file '.\secrets\api_key.txt' --decision-module my_agent
```

For Azure, add `--endpoint 'https://YOUR-APP.azurecontainerapps.io/mcp'` and use a local file containing the **mcp-api-key**, retrieved securely through your operator workflow. Store the model provider credential on the agent host, not in browser settings. The command writes a unique `outputs/research-TIMESTAMP-ID.json`. No model provider is selected or billed by this implementation until you supply and run your adapter.

You can instead connect an existing MCP-capable agent directly to these tools. Give it the workflow instructions in `app/research.py`; the server still enforces browser action limits even if the agent ignores its own prompt.

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

CI covers snapshots, stale/retargeted references, foreign/transaction destinations, ownership, expiration, budgets, process cleanup, MCP dispatch, output evidence and deterministic orchestration. The Docker suite additionally exercises a real MCP session and a synthetic search/product DOM in Chrome on public example.com, without disabling URL policy. These are tool/contract tests, not real LLM evaluations. Follow `docs/CI.md` before assessing model accuracy, variant matching, injection resistance and costs with a chosen model.
