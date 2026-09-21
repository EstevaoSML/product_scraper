"""Regression coverage for actionable failures and durable, redacted logs."""
import json
import socket
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from selenium.common.exceptions import TimeoutException, WebDriverException

from app import error_log, main, policy

KEY = "secret-key-" * 4


@pytest.fixture
def logged_client(monkeypatch):
    path = Path("reports") / ("unit-error-" + uuid.uuid4().hex + ".jsonl")
    monkeypatch.setenv("ERROR_LOG_FILE", str(path))
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.delenv("API_KEY_FILE", raising=False)
    monkeypatch.setattr(main, "last_started", 0)
    monkeypatch.setattr(main, "ALLOWED_HOSTS", {"www.pontofrio.com.br"})
    monkeypatch.setattr(policy.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))])
    try:
        with TestClient(main.app) as client:
            yield client, path
    finally:
        for suffix in ("", ".1", ".2", ".3", ".4", ".5"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)


def post(client, url="https://www.pontofrio.com.br/product?token=secret-query"):
    return client.post("/scrape", json={"url": url}, headers={"X-API-Key": KEY})


def check_retailer_allowlist_and_diagnostics(logged_client, monkeypatch):
    client, path = logged_client
    browser = Mock(return_value={"html": "<html>secret-html</html>"})
    monkeypatch.setattr(main, "scrape_page", browser)
    rejected = post(client, "https://unapproved.invalid/secret-path?token=secret-query")
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "host_not_allowed"
    browser.assert_not_called()
    assert post(client).status_code == 200
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["request_id"] == rejected.json()["request_id"] == rejected.headers["X-Request-ID"]
    assert row["code"] == "host_not_allowed"
    assert row["timestamp"] and row["endpoint"] == "/scrape"
    for secret in (KEY, "secret-path", "secret-query", "secret-html", "unapproved.invalid"):
        assert secret not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind,code", [("dns", "dns_resolution_failed"), ("private", "non_public_destination")])
def check_dns_failures_are_distinct(logged_client, monkeypatch, kind, code):
    client, path = logged_client
    resolver = Mock(side_effect=socket.gaierror("sensitive DNS details")) if kind == "dns" else Mock(return_value=[(None, None, None, None, ("127.0.0.1", 443))])
    monkeypatch.setattr(policy.socket, "getaddrinfo", resolver)
    response = post(client)
    assert response.status_code == 400 and response.json()["code"] == code
    assert "sensitive" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("error,status,code", [
    (TimeoutException("secret exception"), 504, "browser_timeout"),
    (WebDriverException("secret exception"), 502, "browser_error"),
    (RuntimeError("secret exception"), 500, "internal_error"),
    (policy.URLPolicyError("host_not_allowed", "Host is not in ALLOWED_HOSTS"), 400, "redirect_host_not_allowed"),
])
def check_browser_failures_are_logged(logged_client, monkeypatch, error, status, code):
    client, path = logged_client
    monkeypatch.setattr(main, "scrape_page", Mock(side_effect=error))
    response = post(client)
    assert response.status_code == status
    assert response.json()["code"] == code
    assert json.loads(path.read_text(encoding="utf-8"))["code"] == code
    assert "secret exception" not in path.read_text(encoding="utf-8")


def check_early_rejections_and_storage_reopen(logged_client):
    client, path = logged_client
    assert client.post("/scrape", json={"url": "https://example.com"}).status_code == 401
    assert client.post("/scrape", content="secret-body" * 2000).status_code == 413
    assert client.post("/scrape", headers={"X-API-Key": KEY}, json={"url": "short"}).status_code == 422
    original = path.read_text(encoding="utf-8")
    error_log.configure()
    assert client.get("/secret-path?token=secret-query").status_code == 404
    text = path.read_text(encoding="utf-8")
    assert text.startswith(original)
    assert [json.loads(line)["code"] for line in text.splitlines()] == ["unauthorized", "request_too_large", "invalid_request", "not_found"]
    assert "secret" not in text


def check_rotation_is_bounded(logged_client):
    client, path = logged_client
    handler = next(h for h in error_log.logger.handlers if hasattr(h, "maxBytes"))
    handler.maxBytes = 1
    for _ in range(9):
        client.get("/missing")
    assert path.exists() and Path(str(path) + ".5").exists()
    assert not Path(str(path) + ".6").exists()
