import io
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
import yaml

from scripts import publish_ci_images as publish, verify_azure_logs as logs
from app import error_log


def report():
    project = 'scraper-ci-' + 'a'*12
    return {'passed': True, 'project': project, 'images': {'api': project+':local', 'browser': project+'-browser:local'}}


@pytest.mark.parametrize('change', ['failed', 'foreign_image', 'foreign_project'])
def check_publisher_rejects_untested_images(monkeypatch, change):
    value = report()
    if change == 'failed': value['passed'] = False
    if change == 'foreign_image': value['images']['api'] = 'untrusted:latest'
    if change == 'foreign_project': value['project'] = 'production'
    command = Mock()
    monkeypatch.setattr(publish.subprocess, 'run', command)
    with pytest.raises(ValueError): publish.publish('scraperacr.azurecr.io', 'ci-1-'+'b'*40, value)
    command.assert_not_called()


def check_publisher_pushes_and_locks_exact_ci_images(monkeypatch):
    command = Mock()
    monkeypatch.setattr(publish.subprocess, 'run', command)
    publish.publish('scraperacr.azurecr.io', 'ci-1-'+'b'*40, report())
    calls = [call.args[0] for call in command.call_args_list]
    assert calls[0][:3] == ['docker', 'tag', report()['images']['api']]
    assert calls[3][:3] == ['docker', 'tag', report()['images']['browser']]
    assert all('--write-enabled' in calls[i] and '--delete-enabled' in calls[i] for i in (2, 5))
    command.side_effect = subprocess.CalledProcessError(1, 'docker')
    with pytest.raises(subprocess.CalledProcessError): publish.publish('scraperacr.azurecr.io', 'ci-1-'+'b'*40, report())


def check_release_pipeline_uses_tested_images_saved_plan_and_distinct_identities():
    root = Path(__file__).resolve().parents[1]
    pipeline = yaml.safe_load((root/'azure-pipelines.yml').read_text(encoding='utf-8'))
    build, deploy = pipeline['stages']
    assert 'PullRequest' in build['condition'] and 'refs/heads/main' in build['condition']
    steps = build['jobs'][0]['steps']
    assert steps[0]['parameters']['keepImages'] is True
    task = next(s for s in steps if s.get('task') == 'AzureCLI@2')
    assert task['inputs']['azureSubscription'] == '$(PLAN_SERVICE_CONNECTION)'
    assert 'publish_ci_images.py' in task['inputs']['inlineScript']
    assert '-lock=false' in task['inputs']['inlineScript']
    steps = deploy['jobs'][0]['strategy']['runOnce']['deploy']['steps']
    task = next(s for s in steps if s.get('task') == 'AzureCLI@2')
    script = task['inputs']['inlineScript']
    assert task['inputs']['azureSubscription'] == '$(AZURE_SERVICE_CONNECTION)'
    assert 'apply -input=false -lock-timeout=5m "$PLAN_DIRECTORY/release.tfplan"' in script
    assert ' plan ' not in script
    assert 'verify_azure_logs.py' in script
    ci = yaml.safe_load((root/'.azure-pipelines/ci.yml').read_text())
    assert ci['jobs'][0]['steps'] == [{'template': 'validate.yml'}]


def check_cloud_log_records_have_severity_service_and_revision(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(error_log.sys if hasattr(error_log, 'sys') else __import__('sys'), 'stderr', output)
    monkeypatch.delenv('ERROR_LOG_FILE', raising=False)
    monkeypatch.setenv('LOG_SERVICE', 'scraper-api')
    monkeypatch.setenv('CONTAINER_APP_REVISION', 'revision-ci')
    error_log.configure()
    try:
        error_log.write(request_id='request-fixture', status=502, code='browser_error', message='Browser unavailable')
        value = json.loads(output.getvalue())
        assert value['severity'] == 'ERROR' and value['service'] == 'scraper-api'
        assert value['revision'] == 'revision-ci' and value['request_id'] == 'request-fixture'
    finally: error_log.close()


def check_log_probe_is_unauthenticated_and_correlated(monkeypatch):
    request_id = '00000000-0000-0000-0000-000000000001'
    failure = HTTPError('https://app.example/scrape', 401, 'Unauthorized', {},
                        io.BytesIO(json.dumps({'code': 'unauthorized', 'request_id': request_id}).encode()))
    opener = Mock()
    opener.open.side_effect = [io.BytesIO(b'{"status":"ready"}'), failure]
    monkeypatch.setattr(logs, 'OPENER', opener)
    assert logs.emit_error('https://app.example/mcp') == request_id
    assert 'X-api-key' not in opener.open.call_args.args[0].headers


@pytest.mark.parametrize('endpoint', ['http://app.example/mcp', 'https://user:pass@app.example/mcp', 'https://app.example/other'])
def check_probe_rejects_invalid_endpoint(endpoint):
    with pytest.raises(ValueError): logs.emit_error(endpoint)


def check_ingestion_waits_for_matching_error_or_fails(monkeypatch):
    monkeypatch.setattr(logs, 'emit_error', lambda _: '00000000-0000-0000-0000-000000000001')
    monkeypatch.setattr(logs.time, 'sleep', lambda _: None)
    command = Mock(side_effect=[subprocess.CompletedProcess([], 1, '', 'table not yet available'),
                               subprocess.CompletedProcess([], 0, '[{"Matches":1}]', '')])
    monkeypatch.setattr(logs.subprocess, 'run', command)
    logs.verify('https://app.example/mcp', '00000000-0000-0000-0000-000000000002', 'scraper-mcp')
    query = command.call_args.args[0]
    assert '00000000-0000-0000-0000-000000000001' in query[query.index('--analytics-query')+1]
    command.side_effect = None
    command.return_value = subprocess.CompletedProcess([], 0, '[{"Matches":0}]', '')
    with pytest.raises(RuntimeError, match='not confirmed'):
        logs.verify('https://app.example/mcp', '00000000-0000-0000-0000-000000000002', 'scraper-mcp', wait_seconds=0)
