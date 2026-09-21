"""Build and check an isolated Compose stack. Requires Python 3.12+ and Docker.

No third-party Python packages required. Uses temporary keys, an ephemeral host
port, and a unique project/image name, then removes only its own resources.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
OPENER = build_opener(ProxyHandler({}))  # Localhost should never use a system proxy.


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_command(command, **kwargs):
    # Docker emits UTF-8, including bind-mount paths such as Área de Trabalho.
    # Windows' default cp1252 can fail inside a subprocess reader thread and
    # leave stdout as None. Capture bytes and decode in this thread instead.
    result = subprocess.run(command, **kwargs)
    for stream in ("stdout", "stderr"):
        value = getattr(result, stream)
        if isinstance(value, bytes):
            try:
                setattr(result, stream, value.decode("utf-8"))
            except UnicodeDecodeError as error:
                raise RuntimeError(f"Command returned invalid UTF-8 on {stream}") from error
    return result


def inspect_json(*arguments):
    result = run_command(["docker", *arguments], check=True, capture_output=True, timeout=30)
    require(isinstance(result.stdout, str) and bool(result.stdout.strip()),
            "Docker inspection returned no captured output")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Docker inspection returned invalid JSON") from error
    require(isinstance(data, list) and bool(data) and all(isinstance(item, dict) for item in data),
            "Docker inspection did not return a non-empty list of objects")
    return data


def request(base, method, path, key=None, body=None, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    headers.update(extra_headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=180) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def main(keep_images=False):
    # Preflight before creating anything; no Docker state is changed on failure.
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    try:
        run_command(["docker", "info", "--format", "{{.ServerVersion}}"], check=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as error:
        (reports / "container-result.json").write_text(json.dumps({"passed": False,
            "stage": "docker_engine_preflight", "checks": [], "error": str(error)}, indent=2), encoding="utf-8")
        raise
    project = "scraper-ci-" + uuid.uuid4().hex[:12]
    runtime = (ROOT / ".ci-runtime" / project).resolve()
    require(runtime.is_relative_to(ROOT) and runtime.parent.name == ".ci-runtime", "Unexpected temporary directory")
    runtime.mkdir(parents=True)
    key = secrets.token_urlsafe(32)
    key_file = runtime / "api-key.txt"
    proxy_file = runtime / "proxy.txt"
    key_file.write_text(key, encoding="utf-8")
    proxy_file.write_text("", encoding="utf-8")
    # Compose bind-mounted secrets must be readable by the non-root API UID.
    # These are random disposable test credentials, never production secrets.
    key_file.chmod(0o644)
    proxy_file.chmod(0o644)
    env_file = runtime / "empty.env"
    env_file.write_text("", encoding="utf-8")
    env = {**os.environ, "SCRAPER_IMAGE": project + ":local", "SCRAPER_API_PORT": "0", "SCRAPER_BROWSER_IMAGE": project + "-browser:local",
           "SCRAPER_API_KEY_FILE": str(key_file), "SCRAPER_UPSTREAM_PROXY_FILE": str(proxy_file),
           "URL_POLICY": "public", "ALLOWED_HOSTS": "unused.invalid"}
    # Public mode must ignore the old allowlist while enforcing the IP policy.
    compose = ["docker", "compose", "--project-name", project, "--env-file", str(env_file),
               "--file", str(ROOT / "compose.yaml")]
    result = {"project": project, "passed": False, "checks": [], "external_site": "https://example.com"}
    started = False

    def run(*args, capture=False, check=True, timeout=600):
        return run_command([*compose, *args], env=env, cwd=ROOT, check=check,
                           capture_output=capture, timeout=timeout)

    def record(name, condition):
        require(condition, name)
        result["checks"].append(name)
        print("PASS:", name, flush=True)

    try:
        run("config", "--quiet")
        run("build", "api", "chrome")
        started = True
        run("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        port = run("port", "api", "8000", capture=True).stdout.strip().rsplit(":", 1)[1]
        base = "http://127.0.0.1:" + port
        record("API and UC worker ready", request(base, "GET", "/readyz")[0] == 200)
        def mcp_request(method, params=None, auth=key):
            params = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientInfo": {"name": "container-ci", "version": "1"},
                                "io.modelcontextprotocol/clientCapabilities": {}}, **(params or {})}
            headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method,
                       "Accept": "application/json, text/event-stream"}
            if "name" in params:
                headers["Mcp-Name"] = params["name"]
            return request(base, "POST", "/mcp", auth,
                           {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers)
        record("MCP requires authentication", mcp_request("tools/list", auth=None)[0] == 401)
        status, listing = mcp_request("tools/list")
        record("MCP advertises scrape_html", status == 200 and
               any(t["name"] == "scrape_html" for t in listing.get("result", {}).get("tools", [])))
        status, blocked = mcp_request("tools/call", {"name": "scrape_html", "arguments": {"url": "https://127.0.0.1"}})
        record("MCP rejects private addresses", status == 200 and blocked.get("result", {}).get("isError") is True)
        record("Missing API key rejected", request(base, "POST", "/scrape", body={"url": "https://example.com"})[0] == 401)
        record("Wrong API key rejected", request(base, "POST", "/scrape", "wrong", {"url": "https://example.com"})[0] == 401)
        for url in ("https://127.0.0.1", "https://localhost", "https://169.254.169.254", "file:///etc/passwd"):
            record("Rejected " + url, request(base, "POST", "/scrape", key, {"url": url})[0] == 400)
        record("Caller cannot configure proxy", request(base, "POST", "/scrape", key,
               {"url": "https://example.com", "proxy": "http://untrusted.invalid"})[0] == 422)
        status, rejected = request(base, "POST", "/scrape", key, {"url": "https://127.0.0.1"})
        record("URL rejection has diagnostic code and request ID", status == 400 and
               rejected.get("code") == "non_public_destination" and bool(rejected.get("request_id")))
        log_check = ("import json; from pathlib import Path; "
                     "rows=[json.loads(s) for s in Path('/app/logs/errors.jsonl').read_text().splitlines()]; "
                     f"assert any(r['request_id']=={rejected['request_id']!r} and r['code']=='non_public_destination' for r in rows)")
        run("exec", "-T", "api", "python", "-c", log_check)
        run("up", "-d", "--no-deps", "--no-build", "--force-recreate", "--wait", "api")
        # Recreating a container may allocate a different ephemeral host port.
        port = run("port", "api", "8000", capture=True).stdout.strip().rsplit(":", 1)[1]
        base = "http://127.0.0.1:" + port
        run("exec", "-T", "api", "python", "-c", log_check)
        record("Error log survives API container replacement", True)
        # Two successful browser sessions verify cleanup and subsequent reuse.
        for attempt in range(2):
            if attempt:
                time.sleep(3.1)
            arguments = {"url": "https://example.com", "wait_css": "h1", "wait_seconds": 1}
            if attempt:
                status, rpc = mcp_request("tools/call", {"name": "scrape_html", "arguments": arguments})
                require(status == 200 and not rpc.get("result", {}).get("isError"), "MCP browser call failed")
                page = rpc.get("result", {}).get("structuredContent", {})
            else:
                status, page = request(base, "POST", "/scrape", key, arguments)
            record(f"Browser scrape {attempt + 1} returned expected content",
                   status == 200 and page.get("title") == "Example Domain" and
                   "Example Domain" in page.get("html", ""))
            record(f"Browser scrape {attempt + 1} JSON contract",
                   page.get("browser_backend") == "undetected-chromedriver" and
                   page["html_bytes"] == len(page["html"].encode("utf-8")) and
                   str(page.get("browser_version", "")).startswith("152.") and
                   page.get("final_url", "").startswith("https://example.com"))
        status, rpc = mcp_request("tools/call", {"name": "open_page", "arguments": {"url": "https://example.com"}})
        require(status == 200 and not rpc.get("result", {}).get("isError"), "Navigation session open failed")
        session = rpc["result"]["structuredContent"]
        session_id = session["session_id"]
        try:
            record("Navigation returns compact snapshot", "Example Domain" in session["visible_text"] and "html" not in session)
            status, rpc = mcp_request("tools/call", {"name": "inspect_page", "arguments": {"session_id": session_id}})
            record("Navigation retains browser and refreshes references", status == 200 and not rpc["result"].get("isError") and
                   rpc["result"]["structuredContent"]["snapshot_id"] != session["snapshot_id"])
            record("Active session reserves browser capacity", request(base, "POST", "/scrape", key,
                   {"url": "https://example.com"})[0] == 429)
        finally:
            status, rpc = mcp_request("tools/call", {"name": "close_session", "arguments": {"session_id": session_id}})
            record("Navigation session closes", status == 200 and rpc.get("result", {}).get("structuredContent", {}).get("closed") is True)
        run("exec", "-T", "chrome", "/opt/uc/bin/python", "-c", (ROOT / "scripts/navigation_fixture.py").read_text(encoding="utf-8"), timeout=120)
        record("Real Chrome search, follow, structured data and stale reference fixture", True)
        # Exercise the actual gateway from inside the browser's network namespace.
        proxy_check = "import socket; s=socket.create_connection(('egress',8080),5); s.settimeout(5); s.sendall(b'CONNECT 169.254.169.254:443 HTTP/1.1\\r\\nHost: 169.254.169.254:443\\r\\n\\r\\n'); first=s.recv(4096).split(b'\\r\\n')[0]; s.close(); assert b'403' in first, first"
        run("exec", "-T", "chrome", "python3", "-c", proxy_check)
        record("Live egress proxy blocks metadata from Chrome network", True)
        inspect = run("ps", "-q", capture=True).stdout.split()
        require(bool(inspect), "Compose returned no running container IDs")
        containers = inspect_json("inspect", *inspect)
        by_service = {c["Config"]["Labels"]["com.docker.compose.service"]: c for c in containers}
        for service in ("chrome", "egress"):
            record(service + " has no published ports", not any((by_service[service]["NetworkSettings"]["Ports"] or {}).values()))
        browser_networks = by_service["chrome"]["NetworkSettings"]["Networks"]
        record("Chrome only attached to internal network", len(browser_networks) == 1 and
               inspect_json("network", "inspect", next(iter(browser_networks)))[0]["Internal"])
        result["passed"] = True
        result["images"] = {"api": project + ":local", "browser": project + "-browser:local"}
    except Exception as error:
        result["error"] = str(error).replace(key, "[redacted]")
        raise
    finally:
        try:
            if started:
                # Fixed example.com URLs only; don't retain HTML, keys or ambient user logs.
                logs = run("logs", "--no-color", "--tail", "100", capture=True, check=False, timeout=30)
                (reports / "container.log").write_text((logs.stdout + logs.stderr).replace(key, "[redacted]"), encoding="utf-8")
        finally:
            try:
                if started:
                    run("down", "--volumes", "--remove-orphans", timeout=90)
                if not (keep_images and result["passed"]):
                    run_command(["docker", "image", "rm", "--no-prune", project + ":local", project + "-browser:local"],
                                capture_output=True, timeout=30, check=False)
            except (subprocess.SubprocessError, OSError) as error:
                result["passed"] = False
                result["cleanup_error"] = str(error).replace(key, "[redacted]")
                raise
            finally:
                (reports / "container-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                # Only this invocation's UUID-named temporary directory.
                require(runtime.parent == ROOT / ".ci-runtime" and runtime.name == project, "Unexpected cleanup path")
                shutil.rmtree(runtime)
    print("Container checks passed; report: reports/container-result.json", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-images", action="store_true", help="Retain successful CI images for publishing from this same runner")
    try:
        main(keep_images=parser.parse_args().keep_images)
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        print("Container checks FAILED:", error, file=sys.stderr)
        sys.exit(1)
