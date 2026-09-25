"""Private UC worker; Chrome remains on the internal Docker network."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from pydantic import BaseModel, ConfigDict
from app.navigation import TOOLS
from app.sessions import SessionManager

from fastapi import Depends, FastAPI, HTTPException
from app.main import ScrapeRequest, authorize, lifespan, small_requests, http_error, invalid_request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

slot = threading.BoundedSemaphore(1)
manager = SessionManager(slot)


@asynccontextmanager
async def worker_lifespan(application):
    async with lifespan(application):
        manager.start()
        try:
            yield
        finally:
            manager.shutdown()


app = FastAPI(lifespan=worker_lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(small_requests)
app.add_exception_handler(StarletteHTTPException, http_error)
app.add_exception_handler(RequestValidationError, invalid_request)


class NavigationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict
    owner: str


@app.post("/navigate/{action}", dependencies=[Depends(authorize)])
def navigate(action: str, command: NavigationCommand):
    from pydantic import ValidationError
    if action not in TOOLS:
        raise HTTPException(404, "Unknown action")
    try:
        arguments = TOOLS[action][0].model_validate(command.arguments).model_dump()
    except ValidationError:
        raise HTTPException(422, "Invalid navigation arguments") from None
    return manager.execute(action, arguments, command.owner)


@app.get("/healthz")
def health():
    if not all(os.access(os.getenv(name, default), os.X_OK) for name, default in (
        ("CHROME_BINARY", "/usr/bin/google-chrome"), ("CHROMEDRIVER_PATH", "/usr/bin/chromedriver"))):
        raise HTTPException(503, "Browser binaries unavailable")
    return {"status": "ready"}


def run_job(payload):
    with tempfile.TemporaryDirectory(prefix="uc-job-") as directory:
        process = subprocess.Popen([sys.executable, "-m", "app.browser"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True, env={**os.environ, "BROWSER_JOB_DIR": directory})
        try:
            output, _ = process.communicate(payload.model_dump_json().encode(), timeout=150)
            if process.returncode:
                return {"error": "browser_error"}
            return json.loads(output)
        except subprocess.TimeoutExpired:
            return {"error": "browser_timeout"}
        except (ValueError, OSError):
            return {"error": "browser_error"}
        finally:
            # Includes Chrome left behind if UC construction/quit fails or hangs.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()


@app.post("/render", dependencies=[Depends(authorize)])
def render(payload: ScrapeRequest):
    if not slot.acquire(blocking=False):
        raise HTTPException(429, "Browser busy")
    try:
        return run_job(payload)
    finally:
        slot.release()
