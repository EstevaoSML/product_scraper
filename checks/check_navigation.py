import copy
import io
import json
import queue
import threading
from unittest.mock import Mock, MagicMock
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient
from selenium.common.exceptions import StaleElementReferenceException

from app import main, navigation_browser as browser, sessions, browser_worker
from app.navigation import OpenPage, Session, Element, Search, TOOLS

KEY = 'navigation-key-' * 4


class Node:
    def __init__(self, href=None):
        self.href, self.typed = href, []
    def is_displayed(self): return True
    def is_enabled(self): return True
    def clear(self): self.typed.clear()
    def send_keys(self, value): self.typed.append(value)
    def get_attribute(self, name): return self.href


class Driver:
    def __init__(self):
        self.current_url = 'https://shop.example/'
        self.field, self.link = Node(), Node('https://shop.example/ps5')
        self.form_url = self.current_url
        self.text = 'Sony PS5 Digital Edition BRL 3000 in stock'
        self.scripts = [json.dumps({'@graph': [{'@type': 'Product', 'name': 'PS5'}]})]
        self.eligible = True
        self.results_ready_checks = 0
    def get(self, url): self.current_url = url
    def execute_script(self, script, *args):
        if script == browser.SEARCH_CHECK: return self.eligible
        if script == browser.RESULTS_READY:
            self.results_ready_checks += 1
            return True
        if script == 'return document.readyState': return 'complete'
        if script.startswith('return arguments[0].form'): return self.form_url
        assert script == browser.SNAPSHOT
        return {'title': 'Fixture', 'visible_text': self.text, 'text_truncated': False,
                'jsonld': self.scripts[:], 'rows': [
                    {'node': self.field, 'tag': 'input', 'href': None},
                    {'node': self.link, 'tag': 'a', 'href': self.link.href}],
                'page_info': {'product_name': 'Sony PS5 Digital Edition',
                              'description': 'Console digital'}}


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(browser, 'validate_url_structure', lambda url, *a: url)
    monkeypatch.setattr(browser.time, 'sleep', lambda _: None)
    page = browser.BrowserSession(Driver())
    page.execute('open_page', {'url': 'https://shop.example/'})
    return page


def action_args(page, element='e0'):
    return {'snapshot_id': page.snapshot_id, 'element_id': element, 'query': 'PS5'}


def check_isolated_browser_navigation_needs_no_local_dns(monkeypatch):
    from app import policy
    resolver = Mock(side_effect=OSError('isolated network'))
    monkeypatch.setattr(policy.socket, 'getaddrinfo', resolver)
    monkeypatch.setattr(browser.time, 'sleep', lambda _: None)
    monkeypatch.setenv('URL_POLICY', 'public')
    page = browser.BrowserSession(Driver())
    result = page.execute('open_page', {'url': 'https://shop.example/'})
    assert result['status'] == 'ok'
    assert page.execute('follow_link', action_args(page, 'e1'))['url'].endswith('/ps5')
    resolver.assert_not_called()
    with pytest.raises(policy.URLPolicyError) as failure:
        policy.validate_url('https://shop.example/')
    assert failure.value.code == 'dns_resolution_failed'


@pytest.mark.parametrize('url', ['http://shop.example/', 'https://shop.example:8443/',
                               'https://user:secret@shop.example/', 'javascript:alert(1)'])
def check_isolated_browser_still_rejects_unsafe_url_syntax(monkeypatch, url):
    from app.policy import URLPolicyError
    monkeypatch.setenv('URL_POLICY', 'public')
    page = browser.BrowserSession(Driver())
    with pytest.raises(URLPolicyError):
        page.execute('open_page', {'url': url})


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'])
def check_proxy_rejects_private_dns_after_browser_structural_check(monkeypatch, address):
    from app import policy, egress
    url = 'https://shop.example/'
    assert policy.validate_url_structure(url) == url
    monkeypatch.setattr(policy.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', (address, 443))])
    connect = Mock()
    monkeypatch.setattr(egress.socket, 'create_connection', connect)
    with pytest.raises(policy.URLPolicyError):
        egress.open_tunnel('shop.example', 443)
    connect.assert_not_called()


def check_search_navigation_snapshots_and_evidence(page):
    first = page.snapshot_id
    result = page.execute('search_site', action_args(page))
    assert page.driver.field.typed[0] == 'PS5'
    assert page.driver.results_ready_checks == 1
    assert result['snapshot_id'] != first
    assert result['products'][0]['name'] == 'PS5'
    assert result['untrusted_content'] is True
    result = page.execute('follow_link', action_args(page, 'e1'))
    assert result['url'].endswith('/ps5') and result['pages_remaining'] == 4
    assert page.execute('inspect_page', {})['visible_text'].startswith('Sony')


@pytest.mark.parametrize('mutation,code', [
    ('snapshot', 'stale_snapshot'), ('url', 'stale_snapshot'), ('missing', 'unknown_element'),
    ('disabled', 'stale_snapshot'), ('href', 'stale_snapshot'), ('wrong_action', 'action_not_allowed'),
    ('page_limit', 'page_limit')])
def check_references_cannot_be_replayed_or_retargeted(page, mutation, code):
    args = action_args(page, 'e1')
    if mutation == 'snapshot': args['snapshot_id'] = '0'*32
    if mutation == 'url': page.driver.current_url += 'changed'
    if mutation == 'missing': args['element_id'] = 'e99'
    if mutation == 'disabled': page.driver.link.is_enabled = lambda: False
    if mutation == 'href': page.driver.link.href += '-changed'
    if mutation == 'wrong_action': args['element_id'] = 'e0'
    if mutation == 'page_limit': page.followed = 5
    with pytest.raises(browser.NavigationError) as exc:
        page.execute('follow_link', args)
    assert exc.value.code == code


@pytest.mark.parametrize('url', ['https://other.example/', 'https://shop.example/checkout', 'https://shop.example/%63art', 'https://shop.example/?action=delete'])
def check_foreign_and_transaction_destinations(page, url):
    with pytest.raises(browser.NavigationError): page.check_url(url)
    page.driver.link.href = url
    assert all(e['tag'] != 'a' for e in page.snapshot()['elements'])


def check_search_form_rechecked_before_typing(page):
    page.driver.form_url = 'https://other.example/search'
    with pytest.raises(browser.NavigationError): page.execute('search_site', action_args(page))
    assert not page.driver.field.typed
    page.driver.eligible = False
    with pytest.raises(browser.NavigationError): page.execute('search_site', action_args(page))
    assert not any(e['tag'] == 'input' for e in page.snapshot()['elements'])


def check_malformed_structured_data_and_blocked_content(page):
    page.driver.scripts = ['not json', json.dumps({'@type': ['Product'], 'name': 'PS5'})]
    page.driver.text = 'Access denied'
    assert page.snapshot()['status'] == 'blocked'
    assert page.snapshot()['products'][0]['name'] == 'PS5'
    with pytest.raises(browser.NavigationError): page.execute('execute_script', {})


def check_snapshot_structured_data_limit(page, monkeypatch):
    monkeypatch.setattr(browser, 'MAX_SNAPSHOT', 10)
    assert page.snapshot()['structured_data_truncated']


class Process:
    def __init__(self): self.closed = False
    def close(self): self.closed = True
    def call(self, action, arguments, timeout):
        return {'url': 'https://shop.example/', 'snapshot_id': 'a'*32, 'visible_text': 'PS5', 'elements': []}


@pytest.fixture
def manager():
    instance = sessions.SessionManager(threading.BoundedSemaphore(1), factory=Process)
    yield instance
    instance.shutdown()


def opened(manager): return manager.execute('open_page', {'url': 'https://shop.example'}, 'owner')


def check_session_owner_busy_close_and_reuse(manager):
    data = opened(manager)
    process = manager.active['process']
    with pytest.raises(HTTPException) as busy: opened(manager)
    assert busy.value.status_code == 429
    with pytest.raises(HTTPException) as unauthorized:
        manager.execute('inspect_page', data, 'other-owner')
    assert unauthorized.value.status_code == 404 and not process.closed
    with pytest.raises(HTTPException): manager.execute('inspect_page', {'session_id': 'guess'}, 'owner')
    assert manager.execute('close_session', data, 'owner')['closed']
    assert process.closed
    assert opened(manager)['session_id'] != data['session_id']


def check_budget_is_server_enforced(manager):
    data = opened(manager)
    for _ in range(9): manager.execute('inspect_page', data, 'owner')
    process = manager.active['process']
    with pytest.raises(HTTPException) as error: manager.execute('inspect_page', data, 'owner')
    assert error.value.status_code == 409 and process.closed and manager.active is None


def check_expiration_and_reaper(manager):
    data = opened(manager)
    process = manager.active['process']
    manager.clock = lambda: manager.active['deadline'] + 1 if manager.active else 0
    with pytest.raises(HTTPException): manager.execute('inspect_page', data, 'owner')
    assert process.closed
    data = opened(manager)
    manager.stop = Mock()
    manager.stop.wait.side_effect = [False, True]
    manager.reap()
    assert manager.active is None


@pytest.mark.parametrize('error,status,closed', [
    ('stale_snapshot', 409, False), ('unknown_element', 409, False),
    ('action_not_allowed', 409, False), ('outside_retailer', 400, True),
    ('destination_rejected', 400, True), ('page_limit', 409, True),
    ('browser_error', 502, True), ('browser_timeout', 504, True),
])
def check_navigation_error_recovery_and_cleanup(manager, error, status, closed):
    data = opened(manager)
    process = manager.active['process']
    process.call = lambda *a, **k: {'error': error}
    with pytest.raises(HTTPException) as failure:
        manager.execute('inspect_page', data, 'owner')
    assert failure.value.status_code == status
    assert failure.value.detail['code'] == error
    assert process.closed == closed


@pytest.mark.parametrize('exception', [OSError, ValueError, queue.Empty])
def check_failed_browser_releases_capacity(manager, exception):
    data = opened(manager)
    manager.active['process'].call = Mock(side_effect=exception)
    with pytest.raises(HTTPException) as error: manager.execute('inspect_page', data, 'owner')
    assert error.value.status_code == 502 and manager.active is None
    assert opened(manager)


def check_failed_constructor_and_parallel_action(manager):
    manager.factory = Mock(side_effect=OSError)
    with pytest.raises(HTTPException): opened(manager)
    assert manager.slot.acquire(blocking=False)
    manager.slot.release()
    manager.lock.acquire()
    try:
        with pytest.raises(HTTPException) as error: opened(manager)
        assert error.value.status_code == 429
    finally: manager.lock.release()


def check_process_protocol_has_bounded_output_and_timeout():
    process = sessions.ProcessSession.__new__(sessions.ProcessSession)
    process.process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(b'{"ok":true}\n'))
    process.answers = queue.Queue(maxsize=1)
    process.read_answers()
    assert process.call('inspect_page', {}, 0.01) == {'ok': True}
    with pytest.raises(queue.Empty): process.call('inspect_page', {}, 0.01)
    for value in (b'', b'[]', b'x'*(sessions.MAX_NAVIGATION_BYTES+1)):
        process.answers.put(value)
        with pytest.raises(ValueError): process.call('inspect_page', {}, 0.01)


def check_process_lease_removes_credentials_and_kills_group(monkeypatch):
    directory = Mock(name='directory')
    directory.name = 'isolated-directory'
    process = Mock(pid=12345, stdin=io.BytesIO(), stdout=io.BytesIO())
    spawn = Mock(return_value=process)
    kill = Mock()
    monkeypatch.setenv('API_KEY', 'must-not-reach-child')
    monkeypatch.setenv('API_KEY_FILE', 'must-not-reach-child')
    monkeypatch.setattr(sessions.tempfile, 'TemporaryDirectory', lambda **k: directory)
    monkeypatch.setattr(sessions.subprocess, 'Popen', spawn)
    monkeypatch.setattr(sessions.threading, 'Thread', Mock())
    monkeypatch.setattr(sessions.os, 'killpg', kill, raising=False)
    monkeypatch.setattr(sessions.signal, 'SIGKILL', 9, raising=False)
    lease = sessions.ProcessSession()
    assert spawn.call_args.kwargs['start_new_session']
    assert 'API_KEY' not in spawn.call_args.kwargs['env']
    assert 'API_KEY_FILE' not in spawn.call_args.kwargs['env']
    lease.close()
    kill.assert_called_once_with(12345, 9)
    process.wait.assert_called_once_with(timeout=5)
    directory.cleanup.assert_called_once()


def check_browser_line_protocol_errors_and_cleanup(monkeypatch):
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from app.policy import URLPolicyError
    output = io.StringIO()
    driver = Mock()
    actions = [browser.NavigationError('stale_snapshot'), URLPolicyError('private', 'secret'),
               StaleElementReferenceException('secret'), TimeoutException('secret'),
               WebDriverException('secret'), {'visible_text': 'fixture'}]
    engine = Mock()
    engine.execute.side_effect = actions
    monkeypatch.setenv('BROWSER_JOB_DIR', 'unused')
    monkeypatch.setattr(browser, 'create_driver', lambda directory: driver)
    monkeypatch.setattr(browser, 'BrowserSession', lambda d: engine)
    monkeypatch.setattr(browser.sys, 'stdout', output)
    monkeypatch.setattr(browser.sys, 'stdin', io.StringIO('\n'.join(json.dumps({'action': 'inspect_page', 'arguments': {}}) for _ in actions)))
    browser.main()
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert rows[-1]['visible_text'] == 'fixture' and len(rows) == 6
    assert 'secret' not in output.getvalue()
    driver.quit.assert_called_once()


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv('API_KEY_FILE', raising=False)
    monkeypatch.setenv('API_KEY', KEY)
    monkeypatch.setattr(main, 'validate_url', lambda *a: None)
    with TestClient(main.app) as client: yield client


def check_navigation_rest_schema_and_auth(api, monkeypatch):
    run = Mock(return_value=Response('{"ok":true}', media_type='application/json'))
    monkeypatch.setattr(main, 'execute_navigation', run)
    assert api.post('/navigation/open_page', json={'url': 'https://shop.example'}).status_code == 401
    headers = {'X-API-Key': KEY}
    assert api.post('/navigation/execute_script', headers=headers, json={}).status_code == 404
    assert api.post('/navigation/open_page', headers=headers, json={'url': 'https://shop.example', 'script': 'bad'}).status_code == 422
    assert api.post('/navigation/open_page', headers=headers, json={'url': 'https://shop.example'}).status_code == 200
    run.assert_called_once()


@pytest.mark.parametrize('key', ['short', '@Microsoft.KeyVault(SecretUri=unresolved)'])
def check_backend_key_fails_closed_at_startup(monkeypatch, key):
    monkeypatch.delenv('API_KEY_FILE', raising=False)
    monkeypatch.setenv('API_KEY', KEY)
    monkeypatch.setattr(main, 'BROWSER_API_KEY', key)
    with pytest.raises(RuntimeError):
        with TestClient(main.app):
            pass


def check_navigation_forwarding_separate_key_no_redirect(monkeypatch):
    import urllib.request
    monkeypatch.setattr(main, 'api_key', KEY)
    monkeypatch.setattr(main, 'BROWSER_API_KEY', 'private-backend-key'*3)
    monkeypatch.setattr(main, 'validate_url', lambda *a: None)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = json.dumps({'session_id': 's'*43, 'url': 'https://shop.example'}).encode()
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a: opener)
    assert main.execute_navigation('open_page', OpenPage(url='https://shop.example')).status_code == 200
    request = opener.open.call_args.args[0]
    assert request.get_header('X-api-key') == 'private-backend-key'*3
    assert json.loads(request.data)['owner'] != KEY
    for exc, status in [(HTTPError('url', 302, 'private error', {}, None), 502),
                        (HTTPError('url', 429, 'private error', {}, None), 429), (URLError('secret'), 502)]:
        opener.open.side_effect = exc
        with pytest.raises(HTTPException) as error: main.execute_navigation('open_page', OpenPage(url='https://shop.example'))
        assert error.value.status_code == status and 'secret' not in str(error.value.detail)
    worker_body = json.dumps({'detail': 'Navigation left the selected retailer domain; session closed',
                              'code': 'outside_retailer', 'request_id': 'worker-id'}).encode()
    opener.open.side_effect = HTTPError('url', 400, 'private error', {}, io.BytesIO(worker_body))
    with pytest.raises(HTTPException) as error:
        main.execute_navigation('open_page', OpenPage(url='https://shop.example'))
    assert error.value.status_code == 400
    assert error.value.detail == {'code': 'outside_retailer',
                                  'message': 'Navigation left the selected retailer domain; session closed'}
    unknown_body = json.dumps({'detail': 'private internal detail', 'code': 'unexpected'}).encode()
    opener.open.side_effect = HTTPError('url', 502, 'private error', {}, io.BytesIO(unknown_body))
    with pytest.raises(HTTPException) as error:
        main.execute_navigation('open_page', OpenPage(url='https://shop.example'))
    assert error.value.detail == 'Browser failed'
    opener.open.side_effect = None
    from app.navigation import MAX_NAVIGATION_BYTES
    for bad in (b'[]', b'{}', b'x'*(MAX_NAVIGATION_BYTES+1)):
        opener.open.return_value.__enter__.return_value.read.return_value = bad
        with pytest.raises(HTTPException): main.execute_navigation('open_page', OpenPage(url='https://shop.example'))


def check_worker_validates_commands(monkeypatch):
    manager = Mock()
    monkeypatch.setattr(browser_worker, 'manager', manager)
    manager.execute.return_value = {'closed': True}
    command = browser_worker.NavigationCommand(arguments={'session_id': 's'*43}, owner='owner')
    assert browser_worker.navigate('close_session', command)['closed']
    with pytest.raises(HTTPException): browser_worker.navigate('script', command)
    with pytest.raises(HTTPException): browser_worker.navigate('open_page', command)


def check_mcp_navigation_discovery_and_dispatch(monkeypatch):
    from checks.check_mcp import rpc, KEY as MCP_KEY
    monkeypatch.delenv('API_KEY_FILE', raising=False)
    monkeypatch.setenv('API_KEY', MCP_KEY)
    def navigate_result(action, payload):
        data = {'session_id': 's'*43, 'snapshot_id': 'a'*32,
                'url': 'https://shop.example', 'visible_text': 'untrusted', 'elements': []}
        return Response(json.dumps(data), media_type='application/json')
    navigate = Mock(side_effect=navigate_result)
    monkeypatch.setattr(main, 'execute_navigation', navigate)
    with TestClient(main.app, base_url='http://127.0.0.1:8000') as client:
        names = {tool['name'] for tool in rpc(client).json()['result']['tools']}
        assert set(TOOLS).issubset(names)
        assert 'capture_image' not in names
        response = rpc(client, 'tools/call', {'name': 'open_page', 'arguments': {'url': 'https://shop.example'}})
        assert not response.json()['result'].get('isError')
        assert response.json()['result']['structuredContent']['visible_text'] == 'untrusted'
        assert navigate.call_args.args[0] == 'open_page'
