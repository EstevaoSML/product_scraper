import hmac
import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError
from starlette.exceptions import HTTPException as StarletteHTTPException

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from selenium.common.exceptions import TimeoutException, WebDriverException

from app.policy import URLPolicyError, configured_hosts, validate_url
from app import error_log

BROWSER_API_KEY = os.getenv("BROWSER_API_KEY", "")
BROWSER_URL = os.getenv("BROWSER_URL", "http://chrome:8001")
ALLOWED_HOSTS = configured_hosts(os.getenv("URL_POLICY", "public"), os.getenv("ALLOWED_HOSTS", ""))
MAX_RESPONSE_BYTES = 3_500_000  # Encoded JSON size; below Fabric's 4 MB limit.
slot = threading.BoundedSemaphore(1)
last_started = 0.0
api_key = ""


@asynccontextmanager
async def lifespan(app):
    global api_key
    key_path = os.getenv("API_KEY_FILE")
    api_key = Path(key_path).read_text().strip() if key_path else os.getenv("API_KEY", "")
    if len(api_key) < 32 or api_key.startswith("@Microsoft.KeyVault("):
        raise RuntimeError("Provide an API key of at least 32 characters via API_KEY_FILE or API_KEY")
    if BROWSER_API_KEY and (len(BROWSER_API_KEY) < 32 or BROWSER_API_KEY.startswith("@Microsoft.KeyVault(")):
        raise RuntimeError("Browser credential is invalid or unresolved")
    error_log.configure()
    try:
        async with AsyncExitStack() as stack:
            if getattr(app.state, "mcp_enabled", False):
                server, transport = create_server(ScrapeRequest, lambda payload: scrape(payload), MAX_RESPONSE_BYTES, execute_navigation)
                app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/mcp"]
                app.router.routes.extend(transport.routes)
                await stack.enter_async_context(server.session_manager.run())
            yield
    finally:
        error_log.close()


app = FastAPI(title="Rendered HTML Scraper", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def small_requests(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    if request.url.path.rstrip("/") == "/mcp":
        try:
            authorize(request.headers.get("X-API-Key", ""))
        except HTTPException:
            return failure(request, 401, "unauthorized", "Invalid API key")
    # Bound the actual body, including chunked requests, before JSON parsing.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16_384:
            return failure(request, 413, "request_too_large", "Request body too large")
    request._body = bytes(body)
    try:
        response = await call_next(request)
    except Exception:
        response = failure(request, 500, "internal_error", "Unexpected server error; consult the error log using the request ID")
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.rstrip("/") == "/mcp" and response.status_code >= 400:
        error_log.write(request_id=request.state.request_id, status=response.status_code,
                        code="mcp_transport_error", message="MCP transport rejected the request", endpoint="/mcp")
    return response


def failure(request, status, code, message, headers=None):
    request_id = request.state.request_id
    path = request.url.path
    if path.startswith("/navigate/"):
        endpoint = "/navigate/<action>"
    elif path.startswith("/navigation/"):
        endpoint = "/navigation/<action>"
    else:
        endpoint = path if path in ("/scrape", "/readyz", "/healthz", "/mcp") else "<other>"
    error_log.write(request_id=request_id, status=status, code=code, message=message, endpoint=endpoint)
    return JSONResponse({"detail": message, "code": code, "request_id": request_id}, status_code=status,
                        headers={"X-Request-ID": request_id, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **(headers or {})})


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    codes = {400: "invalid_url", 401: "unauthorized", 404: "not_found", 405: "method_not_allowed", 409: "navigation_conflict", 413: "response_too_large", 429: "browser_busy", 502: "browser_error", 503: "browser_unavailable", 504: "browser_timeout"}
    detail = exc.detail
    code = detail["code"] if isinstance(detail, dict) else codes.get(exc.status_code, "http_error")
    message = detail["message"] if isinstance(detail, dict) else str(detail)
    return failure(request, exc.status_code, code, message, exc.headers)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Do not reflect potentially sensitive input in error messages.
    return failure(request, 422, "invalid_request", "Invalid request fields; see README for accepted values")


def authorize(x_api_key: str = Header(default="")):
    if not api_key or not hmac.compare_digest(x_api_key.encode(), api_key.encode()):
        raise HTTPException(401, "Invalid API key")


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=4096)
    wait_css: str | None = Field(default=None, max_length=500)
    wait_seconds: float = Field(default=5, ge=0, le=10)
    timeout_seconds: int = Field(default=30, ge=5, le=60)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/readyz")
def ready():
    try:
        with urlopen(BROWSER_URL + "/healthz", timeout=2) as response:
            if json.load(response).get("status") != "ready":
                raise ValueError("not ready")
    except Exception:
        raise HTTPException(503, "Browser unavailable") from None
    return {"status": "ready"}


def scrape_page(payload: ScrapeRequest) -> dict:
    request = URLRequest(BROWSER_URL + "/render", data=payload.model_dump_json().encode(),
                         headers={"Content-Type": "application/json", "X-API-Key": BROWSER_API_KEY or api_key}, method="POST")
    try:
        with urlopen(request, timeout=160) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 429:
            raise HTTPException(429, "Browser busy; retry later", headers={"Retry-After": "5"}) from None
        raise WebDriverException("Browser worker unavailable") from None
    except (URLError, TimeoutError):
        raise WebDriverException("Browser worker unavailable") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise HTTPException(413, "Rendered page exceeds the JSON response limit")
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
    except ValueError:
        raise WebDriverException("Invalid worker response") from None
    if result.get("error") == "browser_timeout":
        raise TimeoutException()
    if result.get("error") == "response_too_large":
        raise HTTPException(413, "Rendered page exceeds the JSON response limit")
    if result.get("error") or not isinstance(result.get("final_url"), str) or not isinstance(result.get("html"), str):
        raise WebDriverException("Browser operation failed")
    # The network-isolated worker relies on egress DNS/IP enforcement. The API
    # rechecks the final URL and allowlist before releasing HTML to the caller.
    validate_url(result["final_url"], ALLOWED_HOSTS)
    return result


@app.post("/scrape", dependencies=[Depends(authorize)])
def scrape(payload: ScrapeRequest):
    global last_started
    try:
        validate_url(payload.url, ALLOWED_HOSTS)
    except URLPolicyError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from None
    except (ValueError, OSError, UnicodeError):
        raise HTTPException(400, "URL is not allowed or cannot resolve to a public destination") from None
    if not slot.acquire(blocking=False):
        raise HTTPException(429, "Browser busy; retry later", headers={"Retry-After": "5"})
    try:
        if time.monotonic() - last_started < 3:
            raise HTTPException(429, "Wait between requests", headers={"Retry-After": "3"})
        last_started = time.monotonic()
        result = scrape_page(payload)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise HTTPException(413, "Rendered page exceeds the JSON response limit; use a narrower source page")
        return Response(encoded, media_type="application/json")
    except TimeoutException:
        raise HTTPException(504, "Page load or selector wait timed out") from None
    except URLPolicyError as exc:
        raise HTTPException(400, {"code": "redirect_" + exc.code, "message": "Final redirect rejected: " + str(exc)}) from None
    except (ValueError, OSError, UnicodeError):
        raise HTTPException(400, "Redirect destination is not allowed") from None
    except WebDriverException:
        raise HTTPException(502, "Browser operation failed; check browser availability and the target site") from None
    finally:
        slot.release()


def execute_navigation(action, payload):
    from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    arguments = payload.model_dump()
    if action == "open_page":
        try:
            validate_url(payload.url, ALLOWED_HOSTS)
        except URLPolicyError as exc:
            raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from None
    # Session ownership follows the authenticated deployment credential, not caller-supplied identity.
    owner = hashlib.sha256(api_key.encode()).hexdigest()
    request = URLRequest(BROWSER_URL + "/navigate/" + action,
        data=json.dumps({"arguments": arguments, "owner": owner}).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": BROWSER_API_KEY or api_key}, method="POST")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=70) as response:
            raw = response.read(600_001)
    except HTTPError as exc:
        statuses = {400: "Destination rejected", 404: "Session unavailable",
                    409: "Navigation rejected; inspect page or open a new session",
                    422: "Invalid navigation arguments", 429: "Browser busy", 502: "Browser failed",
                    503: "Browser unavailable", 504: "Browser timed out"}
        status = exc.code if exc.code in statuses else 502
        detail = statuses.get(status, "Browser failed")
        try:
            worker_error = json.loads(exc.read(4097))
            code = worker_error.get("code")
            message = worker_error.get("detail")
            allowed_codes = {"destination_rejected", "outside_retailer", "browser_timeout", "browser_error",
                             "stale_snapshot", "unknown_element", "action_not_allowed", "page_limit"}
            if code in allowed_codes and isinstance(message, str) and len(message) <= 300:
                detail = {"code": code, "message": message}
        except (ValueError, AttributeError, OSError):
            pass
        raise HTTPException(status, detail,
                            headers={"Retry-After": "3"} if status == 429 else None) from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(502, "Browser worker unavailable") from None
    try:
        data = json.loads(raw)
        if len(raw) > 600_000 or not isinstance(data, dict) or "session_id" not in data:
            raise ValueError()
        if action != "close_session":
            validate_url(data["url"], ALLOWED_HOSTS)
    except (ValueError, KeyError, UnicodeError):
        raise HTTPException(502, "Invalid browser response") from None
    return Response(raw, media_type="application/json")


@app.post("/navigation/{action}", dependencies=[Depends(authorize)])
def navigation(action: str, arguments: dict):
    from app.navigation import TOOLS
    from pydantic import ValidationError
    if action not in TOOLS:
        raise HTTPException(404, "Unknown navigation action")
    try:
        payload = TOOLS[action][0].model_validate(arguments)
    except ValidationError:
        raise HTTPException(422, "Invalid navigation arguments") from None
    return execute_navigation(action, payload)


from app.mcp_server import create_server

app.state.mcp_enabled = True
