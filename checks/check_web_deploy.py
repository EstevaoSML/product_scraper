"""Execute the deployment's control flow with no Azure or Terraform mutations."""
from pathlib import Path
import shutil
import subprocess
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'webapp/deploy/deploy.ps1'


def powershell(source):
    executable = shutil.which('pwsh')
    if executable is None:
        pytest.skip('PowerShell 7 is required for deployment control-flow tests')
    return subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command', source],
                          capture_output=True, text=True, encoding='utf-8', timeout=30)


def check_validation_needs_no_config_or_azure_login():
    path = str(SCRIPT).replace("'", "''")
    result = powershell("""
$ErrorActionPreference = 'Stop'
function terraform {
    if ($args -contains 'apply') { throw 'Apply forbidden in validation' }
    Write-Output ('TERRAFORM ' + ($args -join ' '))
    $global:LASTEXITCODE = 0
}
function az { throw 'Azure CLI forbidden in local validation' }
function python { throw 'Python installation forbidden in local validation' }
& '""" + path + "' -Config 'this-config-does-not-exist.json'\n")
    assert result.returncode == 0, result.stderr
    assert 'Local validation complete' in result.stdout
    assert result.stdout.count(' validate') == 2
    assert '-test-directory=verification' in result.stdout


@pytest.mark.parametrize('status,success', [('ready', True), ('initializing', False)])
def check_readiness_requires_explicit_ready(status, success):
    # Run the actual bounded readiness block, not a reimplementation of it.
    block = SCRIPT.read_text(encoding='utf-8').split('$siteReady = $false', 1)[1]
    result = powershell("""
$ErrorActionPreference = 'Stop'
$url = 'https://fixture.invalid'
function Start-Sleep { }
function Invoke-RestMethod { return @{status='""" + status + """'} }
$siteReady = $false
""" + block)
    assert (result.returncode == 0) == success
    assert ('Deployment complete:' in result.stdout) == success
    if not success:
        assert 'Website did not report ready' in result.stderr


def check_validation_stops_when_terraform_fails():
    path = str(SCRIPT).replace("'", "''")
    result = powershell("""
$ErrorActionPreference = 'Stop'
function terraform { $global:LASTEXITCODE = 1 }
function az { throw 'Azure CLI forbidden in local validation' }
& '""" + path + "' -Config 'this-config-does-not-exist.json'\n")
    assert result.returncode != 0
    assert 'Local validation complete' not in result.stdout
    assert 'terraform failed' in result.stderr
