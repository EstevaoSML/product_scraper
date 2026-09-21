"""Azure Functions MCP edge: forward only to the configured private scraper."""
from contextlib import asynccontextmanager
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from app import main
from app.mcp_server import create_server


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the backend credential to a redirect target.


opener = build_opener(ProxyHandler({}), NoRedirect())


def backend_settings():
    url = os.getenv("SCRAPER_BACKEND_URL", "").rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment or parts.port not in (None, 443):
        raise RuntimeError("SCRAPER_BACKEND_URL must be a fixed HTTPS origin on port 443")
    key = os.getenv("SCRAPER_BACKEND_KEY", "")
    if len(key) < 32 or key.startswith("@Microsoft.KeyVault("):
        raise RuntimeError("Backend key is missing or its Key Vault reference is unresolved")
    return url, key


def forward_scrape(payload):
    url, key = backend_settings()
    request = Request(url + "/scrape", data=payload.model_dump_json().encode(), method="POST",
                      headers={"Content-Type": "application/json", "X-API-Key": key})
    try:
        with opener.open(request, timeout=170) as response:
            raw = response.read(main.MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        # Do not reflect backend response bodies or URLs into agent-visible errors.
        statuses = {400: "Backend rejected the URL", 413: "Backend response exceeds the size limit",
                    422: "Backend rejected the request fields", 429: "Browser busy; retry later",
                    502: "Browser operation failed", 503: "Browser unavailable", 504: "Browser timed out"}
        status = exc.code if exc.code in statuses else 502
        raise HTTPException(status, statuses.get(status, "Backend request failed"),
                            headers={"Retry-After": "5"} if status == 429 else None) from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(502, "Private scraper backend unavailable") from None
    if len(raw) > main.MAX_RESPONSE_BYTES:
        raise HTTPException(413, "Backend response exceeds the size limit")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("html"), str) or not isinstance(data.get("final_url"), str):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise HTTPException(502, "Invalid backend response") from None
    return Response(raw, media_type="application/json")


@asynccontextmanager
async def lifespan(application):
    backend_settings()
    async with main.lifespan(application):
        server, transport = create_server(main.ScrapeRequest, forward_scrape, main.MAX_RESPONSE_BYTES)
        application.router.routes[:] = [r for r in application.router.routes if getattr(r, "path", None) != "/mcp"]
        application.router.routes.extend(transport.routes)
        async with server.session_manager.run():
            yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(main.small_requests)


@app.get("/healthz")
def health():
    return {"status": "ok"}
