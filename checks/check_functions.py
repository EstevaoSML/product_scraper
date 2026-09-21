import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import functions_app as edge, main


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("SCRAPER_BACKEND_URL", "https://private-backend.example")
    monkeypatch.setenv("SCRAPER_BACKEND_KEY", "backend-key-" * 4)
    monkeypatch.setenv("API_KEY", "client-key-" * 4)
    monkeypatch.delenv("API_KEY_FILE", raising=False)


@pytest.mark.parametrize("url", ["http://private.example", "https://u:p@private.example", "https://private.example/path", "https://private.example:444", "https://private.example?token=secret"])
def check_backend_origin_is_fixed_https(settings, monkeypatch, url):
    monkeypatch.setenv("SCRAPER_BACKEND_URL", url)
    with pytest.raises(RuntimeError):
        edge.backend_settings()


def check_unresolved_references_fail_closed(settings, monkeypatch):
    monkeypatch.setenv("SCRAPER_BACKEND_KEY", "@Microsoft.KeyVault(SecretUri=https://vault/secrets/key)")
    with pytest.raises(RuntimeError):
        edge.backend_settings()
    monkeypatch.setenv("API_KEY", "@Microsoft.KeyVault(SecretUri=https://vault/secrets/key)")
    with pytest.raises(RuntimeError), TestClient(main.app):
        pass


def check_forward_keeps_client_key_out_of_backend_request(settings, monkeypatch):
    open_request = Mock(return_value=io.BytesIO(b'{"html":"ok","final_url":"https://example.com"}'))
    monkeypatch.setattr(edge.opener, "open", open_request)
    response = edge.forward_scrape(main.ScrapeRequest(url="https://example.com"))
    assert json.loads(response.body)["html"] == "ok"
    request = open_request.call_args.args[0]
    assert request.full_url == "https://private-backend.example/scrape"
    assert request.get_header("X-api-key") == "backend-key-" * 4
    assert edge.NoRedirect().redirect_request(None,None,302,"",{},"https://evil.example") is None


@pytest.mark.parametrize("status,expected", [(302,502),(401,502),(400,400),(429,429),(504,504)])
def check_backend_failures_never_reflect_secrets(settings, monkeypatch, status, expected):
    monkeypatch.setattr(edge.opener, "open", Mock(side_effect=HTTPError("https://private",status,"secret",{},io.BytesIO(b'secret'))))
    with pytest.raises(HTTPException) as error:
        edge.forward_scrape(main.ScrapeRequest(url="https://example.com"))
    assert error.value.status_code == expected
    assert "secret" not in str(error.value.detail)


@pytest.mark.parametrize("data", [b'not-json',b'[]',b'{"html":123}'])
def check_invalid_backend_response(settings, monkeypatch, data):
    monkeypatch.setattr(edge.opener, "open", lambda *a,**k: io.BytesIO(data))
    with pytest.raises(HTTPException) as error:
        edge.forward_scrape(main.ScrapeRequest(url="https://example.com"))
    assert error.value.status_code == 502


def check_gateway_mcp_and_limits(settings, monkeypatch):
    monkeypatch.setenv("WEBSITE_HOSTNAME", "azure-generated-host.azurewebsites.net")
    monkeypatch.setattr(edge.opener, "open", lambda *a,**k: io.BytesIO(b'{"html":"ok","final_url":"https://example.com"}'))
    meta = {"io.modelcontextprotocol/protocolVersion":"2026-07-28", "io.modelcontextprotocol/clientInfo":{"name":"ci","version":"1"}, "io.modelcontextprotocol/clientCapabilities":{}}
    body = {"jsonrpc":"2.0", "id":1, "method":"tools/call", "params":{"_meta":meta,"name":"scrape_html","arguments":{"url":"https://example.com"}}}
    headers = {"X-API-Key":"client-key-"*4, "MCP-Protocol-Version":"2026-07-28", "Mcp-Method":"tools/call", "Mcp-Name":"scrape_html", "Accept":"application/json,text/event-stream"}
    with TestClient(edge.app, base_url="https://azure-generated-host.azurewebsites.net") as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/mcp", json=body).status_code == 401
        assert client.post("/scrape", json={}).status_code == 404
        result = client.post("/mcp", headers=headers, json=body)
        assert result.json()["result"]["structuredContent"]["html"] == "ok"
    monkeypatch.setattr(edge.opener,"open", Mock(side_effect=URLError("secret")))
    with pytest.raises(HTTPException):
        edge.forward_scrape(main.ScrapeRequest(url="https://example.com"))
    monkeypatch.setattr(main,"MAX_RESPONSE_BYTES",10)
    monkeypatch.setattr(edge.opener,"open",lambda *a,**k: io.BytesIO(b'x'*11))
    with pytest.raises(HTTPException) as error:
        edge.forward_scrape(main.ScrapeRequest(url="https://example.com"))
    assert error.value.status_code == 413
