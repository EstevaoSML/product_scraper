"""Run actual PowerShell deployment control flow with all cloud calls mocked."""
import json
from pathlib import Path
import shutil
import base64
import subprocess
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



CONFIG = dict(subscription_id='11111111-1111-1111-1111-111111111111', name='fixture01',
              location='eastus', compute_location='eastus2', alert_email='operator@example.com', suggestion_email='')


def write_config(script, changes=None):
    path = script.parents[3] / 'portfolio-deployment.local.json'
    path.write_text(json.dumps(CONFIG | (changes or {})), encoding='utf-8')
    return path


@pytest.mark.parametrize('case', ['missing', 'malformed', 'array', 'subscription', 'name', 'email', 'region', 'unknown', 'whitespace'])
def check_invalid_file_stops_before_tools(tmp_path, case):
    script = script_copy(tmp_path)
    config = write_config(script)
    if case == 'missing': config.unlink()
    elif case == 'malformed': config.write_text('{')
    elif case == 'array': config.write_text('[]')
    else:
        fields = {'subscription': {'subscription_id':'wrong'}, 'name':{'name':'UPPER'},
                  'email':{'alert_email':''}, 'region':{'location':'bad region'},
                  'unknown':{'api_key':'secret-canary'}, 'whitespace':{'name':' fixture01 '}}
        write_config(script, fields[case])
    result = powershell("""
function terraform { throw 'Tool must not run' }
function az { throw 'Tool must not run' }
& 'SCRIPT_PATH' -Action CheckSettings
""".replace('SCRIPT_PATH', str(script).replace("'", "''")))
    assert result.returncode != 0
    assert 'Tool must not run' not in result.stderr
    assert 'secret-canary' not in result.stderr


@pytest.mark.parametrize('shell', ['powershell', 'pwsh'])
@pytest.mark.parametrize('failure', ['none', 'plan', 'apply'])
def check_config_file_authoritative_and_unchanged(tmp_path, shell, failure):
    executable = shutil.which(shell)
    if not executable: pytest.skip(shell + ' unavailable')
    script = script_copy(tmp_path)
    config = write_config(script)
    original = config.read_bytes()
    infra = script.parent / 'terraform'
    # Previously recorded names and an obsolete marker must not block the file.
    state = infra / 'terraform.tfstate'
    state.write_text('{"version":4,"resources":[{"mode":"managed","name":"old-project"}]}')
    marker = infra / '.deployment-identity'
    marker.write_text('old-identity')
    variables = infra / 'portfolio.auto.tfvars.json'
    variables.write_text(json.dumps(dict(name='oldproject', subscription_id='old-subscription',
        deploy_job=True, enable_monthly_schedule=False, package_sha256='abc', budget_start_date='2026-09-01T00:00:00Z')))
    command = """
$ErrorActionPreference = 'Stop'
$env:PRECO_NAME = 'ignored'
$env:PRECO_SUBSCRIPTION_ID = 'ignored'
$env:TF_VAR_name = 'alsoignored'
$env:TF_VAR_subscription_id = 'alsoignored'
function terraform {
    $global:LASTEXITCODE = 0
    if ($args -contains 'plan') {
        $inputFlag = @($args | Where-Object { $_ -like '-var-file=*' })
        if ($inputFlag.Count -ne 1) { throw 'Missing explicit configuration file' }
        $selected = $inputFlag[0].Substring(10)
        if ($selected -ne 'CONFIG_PATH') { throw 'Wrong configuration source' }
        $data = Get-Content -LiteralPath $selected -Raw | ConvertFrom-Json
        if ($data.name -ne 'fixture01') { throw 'Wrong input name' }
    }
    if ($args -contains 'FAIL_AT') { throw 'fixture-failure' }
    if ($args -contains 'output') { return '{}' }
}
function az {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rest') { return '{"subscriptionId":"11111111-1111-1111-1111-111111111111","state":"Enabled"}' }
    if ($args[1] -eq 'show') { return '{"id":"11111111-1111-1111-1111-111111111111","state":"Enabled","environmentName":"AzureCloud"}' }
    if ($args[-1] -ne '11111111-1111-1111-1111-111111111111') { throw 'Azure used environment instead of config' }
}
. 'SCRIPT_PATH' -Action CheckSettings
# Exercise the real Apply-Plan without Azure preflight or package downloads.
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
try { Apply-Plan } catch { if ($_.Exception.Message -ne 'fixture-failure') { throw } }
if ($env:TF_VAR_name -ne 'alsoignored') { throw 'Environment modified' }
Write-Output 'checked'
""".replace('SCRIPT_PATH', str(script).replace("'", "''")).replace('CONFIG_PATH',str(config).replace("'", "''")).replace('FAIL_AT',failure)
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],capture_output=True,timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert config.read_bytes() == original
    assert marker.read_text() == 'old-identity'
    assert 'old-project' in state.read_text()


def check_cloud_action_uses_file_subscription_and_sanitizes_legacy_variables(tmp_path):
    script = script_copy(tmp_path)
    config = write_config(script)
    original = config.read_bytes()
    variables = script.parent / 'terraform/portfolio.auto.tfvars.json'
    variables.write_text(json.dumps(dict(name='oldproject', subscription_id='old', deploy_job=False,
        enable_monthly_schedule=False, package_sha256='', budget_start_date='2026-09-01T00:00:00Z')))
    result = powershell("""
$ErrorActionPreference = 'Stop'
Get-ChildItem Env:ARM_* | Remove-Item
$env:PRECO_SUBSCRIPTION_ID = 'ignored'
function terraform { $global:LASTEXITCODE = 0 }
function az {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rest') { return '{"subscriptionId":"11111111-1111-1111-1111-111111111111","state":"Enabled"}' }
    if ($args[1] -eq 'show') { return '{"id":"11111111-1111-1111-1111-111111111111","state":"Enabled","environmentName":"AzureCloud"}' }
    if ($args[-1] -ne '11111111-1111-1111-1111-111111111111') { throw 'Wrong subscription' }
}
function python { throw 'fixture-stop' }
try { & 'SCRIPT_PATH' -Action Deploy } catch { if ($_.Exception.Message -ne 'fixture-stop') { throw } }
Write-Output 'checked'
""".replace('SCRIPT_PATH',str(script).replace("'", "''")))
    assert result.returncode == 0, result.stderr
    assert config.read_bytes() == original
    assert set(json.loads(variables.read_text())) == {'deploy_job','enable_monthly_schedule','package_sha256','budget_start_date'}
