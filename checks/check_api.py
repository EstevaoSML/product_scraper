from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from selenium.common.exceptions import TimeoutException, WebDriverException
from app import browser, main

KEY = 'a' * 40


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('API_KEY_FILE', raising=False)
    monkeypatch.setenv('API_KEY', KEY)
    monkeypatch.setattr(main, 'last_started', 0)
    monkeypatch.setattr(main, 'validate_url', lambda url, hosts: url)
    with TestClient(main.app) as client:
        yield client


def post(client, **kwargs):
    return client.post('/scrape', headers={'X-API-Key': KEY}, json={'url': 'https://example.com', **kwargs})


def check_auth(client):
    assert client.post('/scrape', json={'url': 'https://example.com'}).status_code == 401


def check_json_success(client, monkeypatch):
    monkeypatch.setattr(main, 'scrape_page', lambda p: {'html': '<html>Olá</html>', 'browser_version': '152'})
    response = post(client)
    assert response.status_code == 200
    assert response.json()['html'] == '<html>Olá</html>'
    assert response.headers['cache-control'] == 'no-store'
    assert post(client).status_code == 429


def check_busy(client):
    main.slot.acquire()
    try:
        assert post(client).status_code == 429
    finally:
        main.slot.release()


@pytest.mark.parametrize('fields', [{'timeout_seconds': 1000}, {'wait_seconds': -1}, {'proxy': 'http://evil'}, {'cookies': []}])
def check_invalid_fields(client, fields):
    assert post(client, **fields).status_code == 422


def check_request_limit(client):
    assert client.post('/scrape', content=b'x' * 20000).status_code == 413


def check_chunked_request_limit(client):
    assert client.post('/scrape', content=iter([b'x' * 10000, b'x' * 10000])).status_code == 413


def check_json_encoded_limit(client, monkeypatch):
    monkeypatch.setattr(main, 'MAX_RESPONSE_BYTES', 50)
    monkeypatch.setattr(main, 'scrape_page', lambda p: {'html': '\"' * 30})
    assert post(client).status_code == 413


def check_timeout_releases_slot(client, monkeypatch):
    monkeypatch.setattr(main, 'scrape_page', Mock(side_effect=TimeoutException('sensitive')))
    response = post(client)
    assert response.status_code == 504
    assert 'sensitive' not in response.text
    assert main.slot.acquire(blocking=False)
    main.slot.release()


def check_driver_cleanup_on_failure(monkeypatch):
    driver = Mock()
    driver.get.side_effect = WebDriverException('failure')
    monkeypatch.setattr(browser.shutil, 'copy2', lambda *a: None)
    monkeypatch.setattr(browser.uc, 'Chrome', lambda **k: driver)
    with pytest.raises(WebDriverException):
        browser.scrape_page(main.ScrapeRequest(url='https://example.com'), 'unused-job')
    driver.quit.assert_called_once()


def check_refuse_missing_key(monkeypatch):
    monkeypatch.delenv('API_KEY_FILE', raising=False)
    monkeypatch.delenv('API_KEY', raising=False)
    with pytest.raises(RuntimeError), TestClient(main.app):
        pass
