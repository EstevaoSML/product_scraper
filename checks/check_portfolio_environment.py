"""Run actual PowerShell deployment control flow with all cloud calls mocked."""
import json
from pathlib import Path
import shutil
import base64
import subprocess
import hashlib
import pytest
from checks.check_web_deploy import powershell


def script_copy(tmp_path):
    directory = tmp_path / 'repo/webapp/deploy/portfolio'
    (directory / 'terraform').mkdir(parents=True)
    script = directory / 'deploy.ps1'
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'webapp/deploy/portfolio/deploy.ps1', script)
    return script


SETTINGS = """
Get-ChildItem Env:ARM_* | Remove-Item
$env:PRECO_SUBSCRIPTION_ID = '11111111-1111-1111-1111-111111111111'
$env:PRECO_NAME = 'fixture01'
$env:PRECO_ALERT_EMAIL = 'operator@example.com'
$env:PRECO_LOCATION = 'eastus'
$env:PRECO_COMPUTE_LOCATION = 'eastus2'
$env:PRECO_SUGGESTION_EMAIL = ''
"""


@pytest.mark.parametrize('value', ['', 'YOUR-AZURE-SUBSCRIPTION-ID', 'wrong'])
def check_invalid_subscription_stops_before_tools(tmp_path, value):
    script = str(script_copy(tmp_path)).replace("'", "''")
    result = powershell(SETTINGS + "$env:PRECO_SUBSCRIPTION_ID = '" + value + "'\n" + """
function terraform { throw 'Terraform must not run' }
function az { throw 'Azure must not run' }
& '""" + script + "' -Action Deploy\n")
    assert result.returncode != 0
    assert 'PRECO_SUBSCRIPTION_ID' in result.stderr
    assert 'must not run' not in result.stderr


def check_environment_forwarding_sanitization_and_restore_on_failure(tmp_path):
    script = script_copy(tmp_path)
    variables = script.parent / 'terraform/portfolio.auto.tfvars.json'
    variables.write_text(json.dumps(dict(subscription_id='11111111-1111-1111-1111-111111111111',
        name='fixture01', alert_email='old@example.com', suggestion_email='public@example.com', location='eastus',
        deploy_job=False, enable_monthly_schedule=False, package_sha256='', budget_start_date='2026-09-01T00:00:00Z')))
    # A stale config file is neither required nor read.
    (script.parent / 'config.local.json').write_text('not valid JSON')
    result = powershell(SETTINGS + """
$env:TF_VAR_subscription_id = 'previous-value'
$env:TF_VAR_alert_email = 'previous-email'
function terraform {
    if ($env:TF_VAR_subscription_id -ne $env:PRECO_SUBSCRIPTION_ID) { throw 'Subscription forwarding failed' }
    if ($env:TF_VAR_alert_email -ne $env:PRECO_ALERT_EMAIL) { throw 'Email forwarding failed' }
    if ($env:TF_VAR_compute_location -ne 'eastus2') { throw 'Compute region forwarding failed' }
    if ($env:TF_VAR_location -ne 'eastus') { throw 'Data region changed' }
    if ($args -contains 'apply') { throw 'No real deployment allowed' }
    $global:LASTEXITCODE = 0
}
function az {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rest') { return (@{subscriptionId=$env:PRECO_SUBSCRIPTION_ID; state='Enabled'} | ConvertTo-Json) }
    if ($args[1] -eq 'show') { return (@{id=$env:PRECO_SUBSCRIPTION_ID; state='Enabled'; environmentName='AzureCloud'} | ConvertTo-Json) }
    if ($args[-1] -ne $env:PRECO_SUBSCRIPTION_ID) { throw 'Wrong Azure subscription' }
}
function python { throw 'fixture-build-stop' }
try { & '""" + str(script).replace("'", "''") + """' -Action Deploy }
catch { if ($_.Exception.Message -ne 'fixture-build-stop') { throw } }
if ($env:TF_VAR_subscription_id -ne 'previous-value' -or $env:TF_VAR_alert_email -ne 'previous-email') { throw 'Environment not restored' }
Write-Output 'Restored successfully'
""")
    assert result.returncode == 0, result.stderr
    assert 'Restored successfully' in result.stdout
    assert set(json.loads(variables.read_text(encoding='utf-8-sig'))) == {
        'deploy_job', 'enable_monthly_schedule', 'package_sha256', 'budget_start_date'}
    assert len((script.parent / 'terraform/.deployment-identity').read_text(encoding='utf-8-sig').strip()) == 64


def check_existing_deployment_rejects_different_environment(tmp_path):
    script = script_copy(tmp_path)
    (script.parent / 'terraform/.deployment-identity').write_text('0' * 64)
    result = powershell(SETTINGS + """
function terraform { throw 'Terraform must not run' }
function az { throw 'Azure must not run' }
& '""" + str(script).replace("'", "''") + "' -Action DisableSchedule\n")
    assert result.returncode != 0
    assert 'another deployment' in result.stderr
    assert 'must not run' not in result.stderr


@pytest.mark.parametrize('shell', ['powershell', 'pwsh'])
def check_json_encoding_and_existing_bom_repair_on_both_shells(tmp_path, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(shell + ' is not installed')
    script = script_copy(tmp_path)
    variables = script.parent / 'terraform/portfolio.auto.tfvars.json'
    original = dict(deploy_job=False, enable_monthly_schedule=False,
                    package_sha256='fixture', budget_start_date='2026-09-01T00:00:00Z')
    variables.write_bytes(b'\xef\xbb\xbf' + json.dumps(original).encode('utf-8'))
    command = """
$ErrorActionPreference = 'Stop'
function terraform {
    if ($args -contains 'output') {
        return @('{', '"label": {"value": "São Paulo — ação"}', '}')
    }
    $bytes = [IO.File]::ReadAllBytes($varsFile)
    if ($bytes[0] -ne 123) { throw 'BOM reached Terraform' }
    if (($args -contains 'apply') -or ($args -contains 'plan')) { throw 'Mutation forbidden' }
    $global:LASTEXITCODE = 0
}
function az { throw 'Azure forbidden' }
. '""" + str(script).replace("'", "''") + """' -Action Validate
Copy-Item -LiteralPath $varsFile -Destination ($varsFile + '.repaired')
Save-Variables @{ deploy_job=$false; enable_monthly_schedule=$false; package_sha256='ação'; budget_start_date='2026-09-01T00:00:00Z' }
Save-Outputs
"""
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert json.loads(Path(str(variables) + '.repaired').read_text(encoding='utf-8')) == original
    assert json.loads(variables.read_text(encoding='utf-8'))['package_sha256'] == 'ação'
    outputs = tmp_path / 'repo/.ci-runtime/portfolio/outputs.json'
    assert json.loads(outputs.read_text(encoding='utf-8'))['label']['value'] == 'São Paulo — ação'


@pytest.mark.parametrize('scenario,expected', [
    ('healthy', 'confirmed'), ('cached_only', 'cannot access this subscription'),
    ('disabled', 'Enabled subscription'), ('override', 'authentication overrides')])
def check_live_subscription_preflight(tmp_path, scenario, expected):
    script = script_copy(tmp_path)
    setup = "$env:ARM_CLIENT_SECRET = 'fixture-credential-canary'\n" if scenario == 'override' else ''
    command = SETTINGS + setup + """
function terraform { $global:LASTEXITCODE = 0 }
function az {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'account') {
        return (@{id=$env:PRECO_SUBSCRIPTION_ID; state='""" + ('Disabled' if scenario == 'disabled' else 'Enabled') + """'; environmentName='AzureCloud'} | ConvertTo-Json)
    }
    if ('""" + scenario + """' -eq 'cached_only') { $global:LASTEXITCODE = 1; return }
    if ('""" + scenario + """' -ne 'healthy') { throw 'Unexpected live request' }
    if ($args[0] -ne 'rest' -or $args[2] -ne 'get') { throw 'Only a read-only ARM request is allowed' }
    return (@{subscriptionId=$env:PRECO_SUBSCRIPTION_ID; state='Enabled'} | ConvertTo-Json)
}
. '""" + str(script).replace("'", "''") + """' -Action Validate
Confirm-AzureSubscription $env:PRECO_SUBSCRIPTION_ID
Write-Output 'confirmed'
"""
    result = powershell(command)
    assert (result.returncode == 0) == (scenario == 'healthy'), result.stderr
    assert expected in result.stdout + result.stderr
    assert 'fixture-credential-canary' not in result.stdout + result.stderr


@pytest.mark.parametrize('scenario,success,message', [
    ('matching', True, 'passed'), ('stale_marker', True, 'passed'),
    ('spaces', True, 'passed'), ('subscription', False, 'PRECO_SUBSCRIPTION_ID differs'),
    ('name', False, 'PRECO_NAME differs'), ('ambiguous', False, 'identity is ambiguous'),
    ('remote', False, 'safe rebind')])
def check_identity_uses_managed_state_without_mutating_it(tmp_path, scenario, success, message):
    script = script_copy(tmp_path)
    infra = script.parent / 'terraform'
    subscription = '11111111-1111-1111-1111-111111111111'
    recorded = '22222222-2222-2222-2222-222222222222' if scenario == 'subscription' else subscription
    group_name = 'rg-another01-portfolio' if scenario == 'name' else 'rg-fixture01-portfolio'
    state = {'version': 4, 'resources': [{'mode':'managed', 'type':'azurerm_resource_group',
        'name':'portfolio', 'instances':[{'attributes': {'id':f'/subscriptions/{recorded}/resourceGroups/{group_name}', 'name':group_name}}]}]}
    if scenario == 'ambiguous':
        state['resources'][0]['type'] = 'azurerm_storage_account'
    state_file = infra / 'terraform.tfstate'
    state_file.write_text(json.dumps(state))
    # Even a matching marker cannot override a different actual managed state.
    marker = infra / '.deployment-identity'
    digest = hashlib.sha256((subscription + '/fixture01').encode()).hexdigest()
    marker.write_text('0' * 64 if scenario in ('stale_marker','remote') else digest)
    if scenario == 'remote':
        (infra / '.terraform').mkdir()
        (infra / '.terraform/terraform.tfstate').write_text('{"backend":{"type":"azurerm"}}')
    record = tmp_path / 'repo/portfolio-deployment.local.json'
    record.write_text('{"preserve":true}')
    before = state_file.read_bytes(), marker.read_bytes(), record.read_bytes()
    extra = "$env:PRECO_SUBSCRIPTION_ID = ' 11111111-1111-1111-1111-111111111111 '; $env:PRECO_NAME = ' fixture01 '" if scenario == 'spaces' else ''
    result = powershell(SETTINGS + extra + """
function terraform { throw 'Terraform forbidden in settings check' }
function az { throw 'Azure forbidden in settings check' }
function python { throw 'Packaging forbidden in settings check' }
& '""" + str(script).replace("'", "''") + "' -Action CheckSettings\n")
    assert (result.returncode == 0) == success, result.stderr
    assert message in result.stdout + result.stderr
    assert (state_file.read_bytes(), marker.read_bytes(), record.read_bytes()) == before


@pytest.mark.parametrize('shell', ['powershell', 'pwsh'])
@pytest.mark.parametrize('failure', ['none', 'plan', 'apply'])
@pytest.mark.parametrize('existing', [True, False])
def check_apply_records_only_allowed_settings(tmp_path, shell, failure, existing):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(shell + ' is not installed')
    script = script_copy(tmp_path)
    record = tmp_path / 'repo/portfolio-deployment.local.json'
    if existing:
        record.write_text('{"previous":true}')
    command = SETTINGS.replace("Get-ChildItem Env:ARM_* | Remove-Item", "") + """
$ErrorActionPreference = 'Stop'
function terraform {
    $global:LASTEXITCODE = 0
    if ($args -contains 'output') { return '{}' }
    if ($args -contains 'FAILURE') { throw 'fixture-failure' }
    if ($args -contains 'apply') {
        $saved = Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json
        if ($saved.status -ne 'apply_started') { throw 'Apply settings not recorded before mutation' }
    }
}
function az { throw 'Azure forbidden' }
. '__SCRIPT_PATH__' -Action Validate
$settings = @{subscription_id=$env:PRECO_SUBSCRIPTION_ID; name=$env:PRECO_NAME;
    location=$env:PRECO_LOCATION; compute_location=$env:PRECO_COMPUTE_LOCATION;
    alert_email='private-canary@example.com'; api_key='secret-canary'}
try { Apply-Plan } catch { if ($_.Exception.Message -ne 'fixture-failure') { throw } }
Write-Output 'checked'
""".replace('__SCRIPT_PATH__', str(script).replace("'", "''")).replace('FAILURE', failure)
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    if failure == 'plan' and not existing:
        assert not record.exists()
        return
    saved = json.loads(record.read_text(encoding='utf-8'))
    if failure == 'plan':
        assert saved == {'previous': True}
    else:
        assert saved['status'] == ('apply_started' if failure == 'apply' else 'apply_succeeded')
        assert saved['environment'] == dict(PRECO_SUBSCRIPTION_ID='11111111-1111-1111-1111-111111111111',
            PRECO_NAME='fixture01', PRECO_LOCATION='eastus', PRECO_COMPUTE_LOCATION='eastus2')
        assert 'canary' not in record.read_text()
        assert saved['recorded_at_utc']
    assert not list(record.parent.glob('portfolio-deployment.local.json.*.tmp'))
