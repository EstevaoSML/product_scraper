"""Regression checks for browser lifecycle, error privacy and network policy."""
import io
import json
import socket
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from selenium.common.exceptions import WebDriverException

from app import browser, egress, main, policy


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('API_KEY_FILE', raising=False)
    monkeypatch.setenv('API_KEY', 'k' * 40)
    monkeypatch.setattr(main, 'last_started', 0)
    with TestClient(main.app) as client:
        yield client


def post(client, url='https://example.com'):
    return client.post('/scrape', json={'url': url}, headers={'X-API-Key': 'k' * 40})


def check_live_api_policy_rejects_private_dns_before_browser(client, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    browser = Mock()
    monkeypatch.setattr(main, 'scrape_page', browser)
    assert post(client).status_code == 400
    browser.assert_not_called()


def check_browser_failure_is_redacted_and_releases_slot(client, monkeypatch):
    monkeypatch.setattr(main, 'validate_url', lambda *a: None)
    monkeypatch.setattr(main, 'scrape_page', Mock(side_effect=WebDriverException('sensitive URL and key')))
    response = post(client)
    assert response.status_code == 502
    assert 'sensitive' not in response.text
    assert main.slot.acquire(blocking=False)
    main.slot.release()


def check_final_url_rejected(client, monkeypatch):
    monkeypatch.setattr(main, 'validate_url', lambda *a: None)
    monkeypatch.setattr(main, 'scrape_page', Mock(side_effect=ValueError('private redirect')))
    assert post(client).status_code == 400


def check_health_and_readiness(client, monkeypatch):
    assert client.get('/healthz').json() == {'status': 'ok'}
    monkeypatch.setattr(main, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps({'status': 'ready'}).encode()))
    assert client.get('/readyz').status_code == 200
    monkeypatch.setattr(main, 'urlopen', Mock(side_effect=OSError('internal address')))
    response = client.get('/readyz')
    assert response.status_code == 503
    assert 'internal address' not in response.text


def check_successful_driver_lifecycle_and_selector(monkeypatch):
    driver = Mock()
    driver.current_url = 'https://example.com/'
    driver.title = 'Example'
    driver.page_source = '<html><h1>Olá</h1></html>'
    driver.capabilities = {'browserVersion': '152.0.7977.82'}
    remote = Mock(return_value=driver)
    monkeypatch.setattr(browser.shutil, 'copy2', lambda *a: None)
    monkeypatch.setattr(browser.uc, 'Chrome', remote)
    monkeypatch.setattr(main, 'validate_url', lambda *a: None)
    monkeypatch.setattr(main.time, 'sleep', lambda _: None)
    result = browser.scrape_page(main.ScrapeRequest(url='https://example.com', wait_css='h1'), 'unused-job')
    assert result['html_bytes'] == len(driver.page_source.encode())
    assert result['browser_version'].startswith('152.')
    driver.find_element.assert_called_with('css selector', 'h1')
    driver.quit.assert_called_once()
    options = remote.call_args.kwargs['options']
    assert any(arg.startswith('--proxy-server=') for arg in options.arguments)
    assert '--proxy-bypass-list=<-loopback>' in options.arguments
    assert not options.to_capabilities().get('acceptInsecureCerts', False)


def check_failed_quit_does_not_discard_success(monkeypatch):
    driver = Mock(current_url='https://example.com/', title='Example', page_source='<html/>', capabilities={})
    driver.quit.side_effect = WebDriverException('already stopped')
    monkeypatch.setattr(browser.shutil, 'copy2', lambda *a: None)
    monkeypatch.setattr(browser.uc, 'Chrome', lambda **k: driver)
    monkeypatch.setattr(main, 'validate_url', lambda *a: None)
    monkeypatch.setattr(main.time, 'sleep', lambda _: None)
    assert browser.scrape_page(main.ScrapeRequest(url='https://example.com'), 'unused-job')['html'] == '<html/>'


def check_egress_fallback_only_uses_prechecked_addresses(monkeypatch):
    monkeypatch.setattr(egress, 'public_addresses', lambda *a: ['2606:4700::1111', '1.1.1.1'])
    monkeypatch.setattr(egress, 'upstream_url', lambda: '')
    reachable = Mock()
    connect = Mock(side_effect=[OSError('IPv4 unavailable'), reachable])
    monkeypatch.setattr(socket, 'create_connection', connect)
    assert egress.open_tunnel('example.com', 443) is reachable
    assert [call.args[0] for call in connect.call_args_list] == [('1.1.1.1', 443), ('2606:4700::1111', 443)]


def check_upstream_rejection_closes_connection(monkeypatch):
    monkeypatch.setattr(egress, 'public_addresses', lambda *a: ['1.1.1.1'])
    monkeypatch.setattr(egress, 'upstream_url', lambda: 'http://proxy.example:8080')
    remote = Mock()
    remote.recv.side_effect = [bytes([b]) for b in b'HTTP/1.1 407 Proxy Authentication Required\r\n\r\n']
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: remote)
    with pytest.raises(OSError):
        egress.open_tunnel('example.com', 443)
    remote.close.assert_called_once()


def check_allowed_url_checks_public_dns(monkeypatch):
    dns = Mock(return_value=['1.1.1.1'])
    monkeypatch.setattr(policy, 'public_addresses', dns)
    assert policy.validate_url('https://EXAMPLE.com/', {'example.com'}) == 'https://EXAMPLE.com/'
    dns.assert_called_once_with('example.com')
