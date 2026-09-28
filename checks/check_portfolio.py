"""No Azure writes, browser launches or model calls; security and budget regressions."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest
from webapp.portfolio import bootstrap, worker
from webapp.portfolio.site import render_site, publish_site
from webapp.cloud_catalog import export_state
from webapp.retail_catalog import RetailCatalog, RETAILERS, import_report
from webapp.deploy.portfolio.package import source_files
from webapp.deploy.portfolio.operations import start_template
from checks.check_web_catalog import report


def ledger():
    return dict(schema_version=1, month='2026-09', attempts={})


def check_monthly_budget_is_idempotent_and_never_refunds_failed_attempts():
    state = ledger()
    for i in range(10):
        for retailer in RETAILERS:
            key = worker.reserve(state, '2026-09', str(i), retailer)
            assert key
            state['attempts'][key]['status'] = 'failed'
            assert worker.reserve(state, '2026-09', str(i), retailer) is None
    assert len(state['attempts']) == 40
    assert sum(a['reserved_cents'] for a in state['attempts'].values()) == 200
    assert worker.reserve(state, '2026-09', 'another-product', 'kabum') is None


@pytest.mark.parametrize('change', [{'month':'2026-10'}, {'schema_version':2}, {'attempts':[]}, {'attempts':{'x':{'reserved_cents':0}}}])
def check_corrupt_ledger_fails_closed(change):
    state = ledger(); state.update(change)
    with pytest.raises(ValueError):
        worker.reserve(state, '2026-09', 'p', 'kabum')


def check_browser_environment_excludes_cloud_and_model_credentials():
    original = dict(PATH='/bin', HOME='/tmp', OPENAI_API_KEY='canary', MCP_API_KEY='canary',
                    IDENTITY_HEADER='canary', IDENTITY_ENDPOINT='http://localhost/token',
                    AZURE_CLIENT_SECRET='canary', PYTHONPATH='/bad', HTTPS_PROXY='http://bad',
                    CHROME_MAJOR='154', CHROME_BINARY='/app/chrome')
    env = worker.child_environment(original)
    assert env == dict(PATH='/bin', HOME='/tmp', CHROME_MAJOR='154', CHROME_BINARY='/app/chrome')
    assert original['OPENAI_API_KEY'] == 'canary'


@pytest.mark.parametrize('name,mode', [('../escape.py',0), ('/root/escape',0), ('C:/escape',0),
                                     ('safe\\..\\escape',0), ('link', stat.S_IFLNK | 0o777)])
def check_archive_traversal_and_symlink_rejected(tmp_path, name, mode):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        entry = zipfile.ZipInfo(name); entry.external_attr = mode << 16
        archive.writestr(entry, b'bad')
    data.seek(0)
    with pytest.raises(ValueError):
        bootstrap.safe_extract(data, tmp_path / 'unpacked')
    assert not (tmp_path / 'unpacked').exists()


def check_archive_extracts_regular_files(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('app/main.py', 'print("fixture")')
    data.seek(0)
    bootstrap.safe_extract(data, tmp_path)
    assert (tmp_path / 'app/main.py').read_text() == 'print("fixture")'


def check_authenticated_package_download_checks_hash_and_refuses_redirects(tmp_path, monkeypatch):
    content = b'package'; digest = hashlib.sha256(content).hexdigest()
    for key, value in dict(CATALOG_STORAGE_ACCOUNT='fixture123', PACKAGE_SHA256=digest,
                           AZURE_CLIENT_ID='fixture-client', IDENTITY_ENDPOINT='http://localhost:42/msi/token',
                           IDENTITY_HEADER='identity-canary').items():
        monkeypatch.setenv(key, value)
    opener = Mock()
    opener.open.side_effect = [io.BytesIO(b'{"access_token":"token-canary"}'), io.BytesIO(content)]
    monkeypatch.setattr(bootstrap, 'build_opener', lambda *args: opener)
    destination = tmp_path / 'pkg.zip'
    bootstrap.download_package(destination)
    requests = [call.args[0] for call in opener.open.call_args_list]
    assert requests[0].headers['X-identity-header'] == 'identity-canary'
    assert requests[1].headers['Authorization'] == 'Bearer token-canary'
    assert requests[1].full_url == f'https://fixture123.blob.core.windows.net/packages/{digest}.zip'
    assert 'identity-canary' not in requests[1].full_url
    opener.open.side_effect = [io.BytesIO(b'{"access_token":"t"}'), io.BytesIO(b'changed')]
    with pytest.raises(ValueError, match='digest'):
        bootstrap.download_package(destination)
    with pytest.raises(ValueError, match='Redirect'):
        bootstrap.NoRedirect().redirect_request(None, None, 302, None, None, 'http://private')


def check_invalid_package_identifier_never_calls_network(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_STORAGE_ACCOUNT', 'evil.invalid/path')
    monkeypatch.setenv('PACKAGE_SHA256', '0' * 64)
    monkeypatch.setenv('AZURE_CLIENT_ID', 'id')
    opener = Mock(); monkeypatch.setattr(bootstrap, 'build_opener', opener)
    with pytest.raises(ValueError):
        bootstrap.download_package(tmp_path / 'package')
    opener.assert_not_called()


def state_with_offer(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps([dict(id='p', query='fixture', name='Fixture', category='Games')]))
    repo = RetailCatalog(tmp_path / 'catalog.sqlite3', manifest)
    import_report(repo.db_path, 'p', report(), 'private-report.json')
    return export_state(repo)


def check_static_export_retains_search_and_averages_without_private_data(tmp_path):
    state = state_with_offer(tmp_path)
    state['runs']['p'] = dict(reason='private-diagnostic-canary', image='/private/file')
    state['observations'][0]['evidence'] = 'private-evidence-canary'
    state['manifest'][0]['name'] = '</script><script>alert(1)</script>'
    original = deepcopy(state)
    files = render_site(state, Mock())
    page = files['index.html'].decode()
    assert 'R$ 100,01' in page and '02/01/2026' in page
    assert 'catalog-data' in page and 'id="search"' in page and 'data-product="p"' in page
    assert 'private-' not in page and '/api/' not in page and '/media/' not in page
    assert '<script>alert(1)</script>' not in page
    assert 'Content-Security-Policy' in page
    assert any(k.endswith('app.js') for k in files)
    assert state == original


def check_public_image_must_match_allowed_path_and_hash(tmp_path):
    state = state_with_offer(tmp_path)
    state['runs']['p'] = dict(image_blob='reports/private.json')
    read = Mock()
    render_site(state, read); read.assert_not_called()
    state['runs']['p']['image_blob'] = 'images/p/' + 'a' * 64 + '.png'
    read.return_value = b'\x89PNG\r\n\x1a\nchanged'
    with pytest.raises(ValueError, match='hash'):
        render_site(state, read)


def check_static_publish_commits_index_last_and_stops_on_lost_lease(tmp_path):
    state = state_with_offer(tmp_path)
    public, private, lease, lost = Mock(), Mock(), Mock(), Mock()
    lost.is_set.return_value = False
    publish_site(public, state, private, lease, lost)
    assert public.upload_blob.call_args_list[-1].args[0] == 'index.html'
    assert public.upload_blob.call_args_list[-1].kwargs['content_settings'].cache_control == 'no-cache'
    public.reset_mock(); lost.is_set.return_value = True
    with pytest.raises(RuntimeError):
        publish_site(public, state, private, lease, lost)
    public.upload_blob.assert_not_called()


def check_four_retailers_reject_impostors_and_different_variants(tmp_path):
    repo = RetailCatalog(tmp_path / 'catalog.sqlite3')
    for i, (retailer, config) in enumerate(RETAILERS.items()):
        assert import_report(repo.db_path, 'ps5-digital', report(str(100 + i * 100), url='https://' + config['host'] + '/item'), 'fixture.json', retailer)
        with pytest.raises(ValueError, match='Untrusted'):
            import_report(repo.db_path, 'ps5-digital', report(url='https://' + config['host'] + '.evil.example/item'), 'fixture.json', retailer)
    item = repo.get_product('ps5-digital')
    assert item['current_cents'] == 25000 and item['first_cents'] == 25000 and item['retailer_count'] == 4
    with pytest.raises(ValueError, match='variant'):
        import_report(repo.db_path, 'ps5-digital', report(variant='Different size', url='https://www.amazon.com.br/item'), 'fixture.json', 'amazon')


def check_source_package_excludes_keys_outputs_git_and_vendor():
    root = Path(__file__).resolve().parents[1]
    paths = [name for name, _ in source_files(root)]
    assert 'app/research_job.py' in paths and 'webapp/portfolio/worker.py' in paths
    assert 'webapp/data/catalog.sqlite3' in paths
    assert not any(part in name for name in paths for part in ('secrets/', '.env', '.git/', 'outputs/', 'vendor/'))


def check_job_override_preserves_image_and_bootstrap():
    job = {'properties': {'template': {'containers': [dict(name='portfolio', image='mcr.microsoft.com/fixture', command=['python3', '-c', 'trusted'], resources={'cpu':2,'memory':'4Gi'}, env=[dict(name='PACKAGE_SHA256',value='hash'), dict(name='RUN_MODE',value='collect')])]}}}
    template = start_template(job, 'smoke', 1)
    container = template['containers'][0]
    assert container['command'] == ['python3', '-c', 'trusted']
    assert {e['name']:e['value'] for e in container['env']} == dict(PACKAGE_SHA256='hash',RUN_MODE='smoke',MAX_TASKS='1')
    with pytest.raises(ValueError): start_template(job, 'collect', 41)


def check_research_subprocess_uses_existing_cli_with_zero_image_budget(tmp_path, monkeypatch):
    @contextmanager
    def services(): yield 'local-mcp-canary'
    monkeypatch.setattr(worker, 'services', services)
    monkeypatch.setenv('IDENTITY_HEADER', 'never-in-child')
    process = Mock(); process.poll.return_value = 0; process.returncode = 0
    launch = Mock(return_value=process); monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    lost = Mock(); lost.is_set.return_value = False
    worker.run_agent({'query':'PS5'}, 'amazon', tmp_path, 'openai-canary', lost)
    command = launch.call_args.args[0]
    assert command[1:3] == ['-m', 'app.research_job']
    assert command[command.index('--url')+1] == 'https://www.amazon.com.br/'
    assert command[command.index('--max-cost-usd')+1] == '0.05'
    assert command[command.index('--image-max-cost-usd')+1] == '0'
    env = launch.call_args.kwargs['env']
    assert env['OPENAI_API_KEY'] == 'openai-canary' and env['MCP_API_KEY'] == 'local-mcp-canary'
    assert 'IDENTITY_HEADER' not in env and 'openai-canary' not in ' '.join(command)
    lost.is_set.return_value = True; launch.reset_mock()
    with pytest.raises(RuntimeError): worker.run_agent({'query':'PS5'}, 'amazon', tmp_path, 'secret', lost)
    launch.assert_not_called()


def check_chrome_major_is_explicit_and_strict(monkeypatch):
    from app import browser
    monkeypatch.setattr(browser.shutil, 'copy2', Mock())
    launch = Mock(); monkeypatch.setattr(browser.uc, 'Chrome', launch)
    monkeypatch.setenv('CHROME_MAJOR', '154')
    browser.create_driver('fixture')
    assert launch.call_args.kwargs['version_main'] == 154
    for invalid in ('154 --bad', '99', '١٥٤', '1000'):
        monkeypatch.setenv('CHROME_MAJOR', invalid)
        with pytest.raises(ValueError): browser.create_driver('fixture')


@pytest.mark.parametrize('exit_code', [0, 1])
def check_reservation_persisted_before_launch_and_rerun_skips(tmp_path, monkeypatch, exit_code):
    from azure.core.exceptions import ResourceNotFoundError
    import azure.keyvault.secrets
    state = state_with_offer(tmp_path)
    state['manifest'] = state['manifest'][:1]
    original_temporary = worker.tempfile.TemporaryDirectory
    monkeypatch.setattr(worker.tempfile, 'TemporaryDirectory', lambda **kw: original_temporary(dir=tmp_path, **kw))
    # Persist a fake ledger across two invocations; failed model attempt remains reserved.
    saved = {}
    ledger_blob = Mock()
    def download():
        if not saved: raise ResourceNotFoundError('fixture')
        return SimpleNamespace(readall=lambda: saved['data'], properties=SimpleNamespace(etag='e'))
    def upload(data, **kwargs):
        saved['data'] = data
        return {'etag':'e'}
    ledger_blob.download_blob.side_effect = download; ledger_blob.upload_blob.side_effect = upload
    container = Mock(); container.get_blob_client.return_value = ledger_blob
    secret = Mock(); secret.__enter__ = Mock(return_value=secret); secret.__exit__ = Mock(return_value=False)
    secret.get_secret.return_value.value = 'fixture-key'
    monkeypatch.setattr(azure.keyvault.secrets, 'SecretClient', lambda *a, **kw: secret)
    monkeypatch.setenv('KEY_VAULT_NAME', 'fixture')
    monkeypatch.setattr(worker, 'RETAILERS', {'kabum': RETAILERS['kabum']})
    launches = []
    def run(seed, retailer, directory, api_key, lost):
        assert json.loads(saved['data'])['attempts']['p/kabum']['status'] == 'reserved'
        launches.append(retailer)
        if exit_code == 0:
            (directory / 'research-fixture.json').write_text(json.dumps(report('125', date='2026-01-03T12:00:00+00:00')))
            (directory / 'diagnostics-fixture.json').write_text(json.dumps({'budgets': {'usd_accounted':'0.001'}}))
        return exit_code
    monkeypatch.setattr(worker, 'run_agent', run)
    monkeypatch.setattr(worker, 'publish', Mock())
    lost = Mock(); lost.is_set.return_value = False
    updated = worker.collect(container, Mock(), Mock(), lost, state, Mock(), 1)
    assert len(updated['observations']) == (2 if exit_code == 0 else 1)
    worker.collect(container, Mock(), Mock(), lost, state, Mock(), 1)
    assert launches == ['kabum']
    assert json.loads(saved['data'])['attempts']['p/kabum']['status'] == ('imported' if exit_code == 0 else 'failed')


def check_validation_script_needs_no_azure_or_configuration():
    from checks.check_web_deploy import powershell
    script = str(Path(__file__).resolve().parents[1] / 'webapp/deploy/portfolio/deploy.ps1').replace("'", "''")
    result = powershell("""
function terraform {
    if ($args -contains 'apply' -or $args -contains 'plan') { throw 'Mutation forbidden' }
    Write-Output ($args -join ' ')
    $global:LASTEXITCODE = 0
}
function az { throw 'Azure forbidden' }
function python { throw 'Package build forbidden' }
& '""" + script + "' -Action Validate\n")
    assert result.returncode == 0, result.stderr
    assert 'validate' in result.stdout


def check_portfolio_has_no_fixed_cost_infrastructure():
    import re
    text = (Path(__file__).resolve().parents[1] / 'webapp/deploy/portfolio/terraform/main.tf').read_text()
    resources = re.findall(r'resource\s+"([^"]+)"', text)
    forbidden = {'azurerm_container_registry', 'azurerm_container_app', 'azurerm_private_endpoint',
                 'azurerm_log_analytics_workspace', 'azurerm_nat_gateway', 'azurerm_service_plan'}
    assert not forbidden.intersection(resources)
    assert resources.count('azurerm_container_app_job') == 1


def check_new_subscription_registers_required_azure_services():
    import re
    text = (Path(__file__).resolve().parents[1] / 'webapp/deploy/portfolio/terraform/main.tf').read_text()
    provider = text.split('provider "azurerm" {', 1)[1].split('data "azurerm_client_config"', 1)[0]
    registration = re.search(r'resource_providers_to_register\s*=\s*\[([^\]]+)\]', provider)
    assert registration, 'Fresh subscriptions require explicit resource-provider registration'
    services = set(re.findall(r'"(Microsoft\.[^"]+)"', registration.group(1)))
    assert {'Microsoft.App', 'Microsoft.Storage', 'Microsoft.KeyVault',
            'Microsoft.ManagedIdentity', 'Microsoft.Consumption', 'Microsoft.OperationalInsights'} <= services
    assert 'Microsoft.ContainerRegistry' not in services
