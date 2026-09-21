import io
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app import main, policy


def check_public_ignores_old_allowlist_and_invalid_mode_fails():
    assert policy.configured_hosts("public", "example.com") is None
    assert policy.configured_hosts("allowlist", " Example.COM, ") == {"example.com"}
    for mode, hosts in [("typo", "example.com"), ("allowlist", " , ")]:
        with pytest.raises(ValueError):
            policy.configured_hosts(mode, hosts)


@pytest.mark.parametrize("url", ["https://www.kabum.com.br/produto/989702", "https://new-retailer.example/product", "https://8.8.8.8/"])
def check_public_accepts_unlisted_public_hosts(monkeypatch, url):
    monkeypatch.setattr(policy, "public_addresses", Mock(return_value=["8.8.8.8"]))
    assert policy.validate_url(url) == url


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["168.63.129.16"], ["::1"], ["fc00::1"], ["8.8.8.8", "192.168.1.1"]])
def check_public_still_rejects_private_and_mixed_dns(monkeypatch, addresses):
    monkeypatch.setattr(policy.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (ip, 443)) for ip in addresses])
    with pytest.raises(policy.URLPolicyError, match="public IP"):
        policy.validate_url("https://unlisted.example")


@pytest.mark.parametrize("url", ["http://example.com", "file:///etc/passwd", "https://example.com:444", "https://user:pass@example.com", "https://example.com\\@localhost", "https://example.com/\nfoo"])
def check_public_still_rejects_unsafe_url_forms(url):
    with pytest.raises(policy.URLPolicyError):
        policy.validate_url(url)


def check_api_accepts_kabum_and_public_redirect_but_rejects_private_redirect(monkeypatch):
    monkeypatch.delenv("API_KEY_FILE", raising=False)
    monkeypatch.setenv("API_KEY", "k" * 40)
    monkeypatch.setattr(main, "ALLOWED_HOSTS", policy.configured_hosts("public", "example.com"))
    def dns(host, *a, **k):
        ip = "127.0.0.1" if host == "localhost" else "8.8.8.8"
        return [(None, None, None, None, (ip, 443))]
    monkeypatch.setattr(policy.socket, "getaddrinfo", dns)
    with TestClient(main.app) as client:
        for final, status in [("https://different-retailer.example/item", 200), ("https://localhost/secret", 400)]:
            monkeypatch.setattr(main, "last_started", 0)
            monkeypatch.setattr(main, "urlopen", lambda *a, **k: io.BytesIO(json.dumps({"html": "<html/>", "final_url": final}).encode()))
            result = client.post("/scrape", headers={"X-API-Key": "k" * 40}, json={"url": "https://www.kabum.com.br/produto/989702"})
            assert result.status_code == status
            if status == 400:
                assert result.json()["code"] == "redirect_non_public_destination"
