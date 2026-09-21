import io
import json
import subprocess
from contextlib import nullcontext
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from selenium.common.exceptions import TimeoutException, WebDriverException

from app import browser, browser_worker, main


def payload():
    return main.ScrapeRequest(url="https://example.com", wait_seconds=0)


@pytest.mark.parametrize("result,exception", [
    ({"error": "browser_timeout"}, TimeoutException),
    ({"error": "browser_error"}, WebDriverException),
    ({"error": "response_too_large"}, HTTPException),
    ({}, WebDriverException),
    ([], WebDriverException),
])
def check_worker_error_mapping(monkeypatch, result, exception):
    monkeypatch.setattr(main, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(result).encode()))
    with pytest.raises(exception):
        main.scrape_page(payload())


def check_worker_response_final_policy_and_auth(monkeypatch):
    page = {"html": "<html/>", "final_url": "https://example.com/"}
    fetch = Mock(return_value=io.BytesIO(json.dumps(page).encode()))
    validate = Mock()
    monkeypatch.setattr(main, "urlopen", fetch)
    monkeypatch.setattr(main, "validate_url", validate)
    monkeypatch.setattr(main, "api_key", "private-test-key")
    assert main.scrape_page(payload()) == page
    validate.assert_called_once_with(page["final_url"], main.ALLOWED_HOSTS)
    assert fetch.call_args.args[0].get_header("X-api-key") == "private-test-key"


@pytest.mark.parametrize("error", [URLError("secret"), TimeoutError("secret"), HTTPError("internal", 500, "secret", {}, None)])
def check_worker_transport_errors_are_redacted(monkeypatch, error):
    monkeypatch.setattr(main, "urlopen", Mock(side_effect=error))
    with pytest.raises(WebDriverException) as caught:
        main.scrape_page(payload())
    assert "secret" not in str(caught.value)


def check_worker_busy_and_oversized(monkeypatch):
    monkeypatch.setattr(main, "urlopen", Mock(side_effect=HTTPError("internal", 429, "busy", {}, None)))
    with pytest.raises(HTTPException) as caught:
        main.scrape_page(payload())
    assert caught.value.status_code == 429
    monkeypatch.setattr(main, "MAX_RESPONSE_BYTES", 10)
    monkeypatch.setattr(main, "urlopen", lambda *a, **k: io.BytesIO(b"x" * 11))
    with pytest.raises(HTTPException) as caught:
        main.scrape_page(payload())
    assert caught.value.status_code == 413


@pytest.mark.parametrize("outcome,returncode,expected", [
    ((b'{"html":"ok"}', None), 0, {"html": "ok"}),
    ((b'bad-json', None), 0, {"error": "browser_error"}),
    ((b'', None), 1, {"error": "browser_error"}),
    (subprocess.TimeoutExpired("job", 150), None, {"error": "browser_timeout"}),
])
def check_job_group_cleanup_even_on_timeout(monkeypatch, outcome, returncode, expected):
    process = Mock(pid=1234567, returncode=returncode)
    process.communicate.side_effect = [outcome, (b"", None)]
    spawn = Mock(return_value=process)
    kill = Mock()
    monkeypatch.setattr(browser_worker.tempfile, "TemporaryDirectory", lambda **k: nullcontext("unused-job"))
    monkeypatch.setattr(browser_worker.subprocess, "Popen", spawn)
    monkeypatch.setattr(browser_worker.os, "killpg", kill, raising=False)
    monkeypatch.setattr(browser_worker.signal, "SIGKILL", 9, raising=False)
    assert browser_worker.run_job(payload()) == expected
    kill.assert_called_once_with(process.pid, 9)
    assert spawn.call_args.kwargs["start_new_session"] is True
    assert process.communicate.call_args_list[0].kwargs["timeout"] == 150


def check_private_worker_auth_busy_and_release(monkeypatch):
    monkeypatch.delenv("API_KEY_FILE", raising=False)
    monkeypatch.setenv("API_KEY", "x" * 40)
    monkeypatch.setattr(browser_worker.os, "access", lambda *a: True)
    monkeypatch.setattr(browser_worker, "run_job", lambda p: {"html": "ok"})
    with TestClient(browser_worker.app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/render", json=payload().model_dump()).status_code == 401
        headers = {"X-API-Key": "x" * 40}
        browser_worker.slot.acquire()
        try:
            assert client.post("/render", headers=headers, json=payload().model_dump()).status_code == 429
        finally:
            browser_worker.slot.release()
        assert client.post("/render", headers=headers, json=payload().model_dump()).json() == {"html": "ok"}
        assert browser_worker.slot.acquire(blocking=False)
        browser_worker.slot.release()


def check_uc_uses_supplied_driver_and_chrome_152(monkeypatch):
    driver = Mock(current_url="https://example.com", page_source="<html/>", title="Example", capabilities={"browserVersion": "152.0"})
    launch = Mock(return_value=driver)
    copy = Mock()
    monkeypatch.setattr(browser.uc, "Chrome", launch)
    monkeypatch.setattr(browser.shutil, "copy2", copy)
    result = browser.scrape_page(payload(), "isolated-job")
    assert result["browser_backend"] == "undetected-chromedriver"
    kwargs = launch.call_args.kwargs
    assert kwargs["version_main"] == 152 and kwargs["use_subprocess"] is True
    assert "isolated-job" in kwargs["driver_executable_path"]
    assert "isolated-job" in kwargs["user_data_dir"]
    assert "--headless=new" in kwargs["options"].arguments
    assert "--proxy-bypass-list=<-loopback>" in kwargs["options"].arguments
    copy.assert_called_once()
    driver.quit.assert_called_once()


@pytest.mark.parametrize("result,error,expected", [
    ({"html": "ok"}, None, {"html": "ok"}),
    (None, TimeoutException("secret"), {"error": "browser_timeout"}),
    (None, RuntimeError("secret"), {"error": "browser_error"}),
    ({"html": "x" * 50}, None, {"error": "response_too_large"}),
])
def check_job_output_contract_and_error_privacy(monkeypatch, result, error, expected):
    output = io.BytesIO()
    monkeypatch.setattr(browser.sys, "stdin", Mock(buffer=io.BytesIO(payload().model_dump_json().encode())))
    monkeypatch.setattr(browser.sys, "stdout", Mock(buffer=output))
    monkeypatch.setenv("BROWSER_JOB_DIR", "unused-job")
    monkeypatch.setattr(main, "MAX_RESPONSE_BYTES", 40)
    monkeypatch.setattr(browser, "scrape_page", Mock(return_value=result, side_effect=error))
    browser.main()
    assert json.loads(output.getvalue()) == expected
    assert b"secret" not in output.getvalue()
