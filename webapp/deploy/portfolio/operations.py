"""Operator-only uploads/secret provisioning/execution; never runs the model locally."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def cli(*args):
    command = shutil.which('az')
    if not command:
        raise RuntimeError('Install Azure CLI')
    result = subprocess.run([command, *args, '--only-show-errors', '-o', 'json'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Azure CLI operation failed; check login, subscription and permissions')
    return json.loads(result.stdout or '{}')


def start_template(job, mode, limit):
    if mode not in ('smoke', 'publish', 'collect') or not 1 <= limit <= 40:
        raise ValueError('Invalid execution override')
    template = job.get('properties', job)['template']
    containers = template['containers']
    if len(containers) != 1 or containers[0]['name'] != 'portfolio':
        raise ValueError('Unexpected job template')
    env = containers[0].setdefault('env', [])
    for name, value in {'RUN_MODE': mode, 'MAX_TASKS': str(limit)}.items():
        env[:] = [entry for entry in env if entry['name'] != name]
        env.append(dict(name=name, value=value))
    return template


def main(args):
    from azure.identity import AzureCliCredential
    from azure.keyvault.secrets import SecretClient
    from azure.storage.blob import BlobServiceClient
    from azure.core.exceptions import HttpResponseError, ResourceExistsError
    if args.action == 'check-dependencies':
        print('Deployment Python dependencies available.')
        return
    if args.outputs is None:
        raise ValueError('--outputs is required for cloud operations')
    values = json.loads(args.outputs.read_text(encoding='utf-8-sig'))
    get = lambda key: values[key]['value']
    with AzureCliCredential() as credential:
        if args.action in ('upload', 'check-ready'):
            with BlobServiceClient(f'https://{get("storage_account")}.blob.core.windows.net', credential,
                                   connection_timeout=10, read_timeout=60, retry_total=2) as service:
                if args.action == 'upload':
                    manifest = json.loads(args.package.read_text(encoding='utf-8'))
                    source = Path(manifest['path'])
                    with source.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if digest != manifest['sha256']:
                        raise ValueError('Package changed since build')
                    blob = service.get_blob_client('packages', digest + '.zip')
                    for attempt in range(16):
                        try:
                            with source.open('rb') as stream:
                                blob.upload_blob(stream, overwrite=False, max_concurrency=2)
                            break
                        except ResourceExistsError:
                            if blob.get_blob_properties().size != source.stat().st_size:
                                raise ValueError('Conflicting immutable package')
                            break
                        except HttpResponseError as exc:
                            if exc.status_code != 403 or attempt == 15:
                                raise
                            time.sleep(20)  # Azure RBAC propagation, not a research retry.
                    print('Private package uploaded: ' + digest)
                else:
                    smoke = json.loads(service.get_blob_client('catalog', 'executions/smoke.json').download_blob().readall())
                    if smoke.get('status') != 'passed' or smoke.get('package_sha256') != get('package_sha256'):
                        raise ValueError('Run Smoke successfully for this package before enabling the schedule')
        if args.action in ('set-secret', 'check-ready'):
            with SecretClient(f'https://{get("vault_name")}.vault.azure.net', credential, logging_enable=False) as client:
                if args.action == 'set-secret':
                    value = getpass.getpass('OpenAI API key (hidden; stored only in Azure Key Vault): ').strip()
                    if not value:
                        raise ValueError('Empty secret')
                    client.set_secret('openai-api-key', value)
                    print('OpenAI key saved in Key Vault; no secret written to Terraform.')
                else:
                    secret = client.get_secret('openai-api-key')
                    if not secret.value or secret.properties.enabled is False:
                        raise ValueError('Provision an enabled OpenAI secret first')
        if args.action == 'start':
            job = cli('containerapp', 'job', 'show', '-g', get('resource_group'), '-n', get('job_name'))
            template = start_template(job, args.mode, args.limit)
            with tempfile.TemporaryDirectory(prefix='portfolio-start-') as scratch:
                path = Path(scratch) / 'execution.json'
                # JSON is valid YAML. Preserve the full template (image, resource,
                # identity settings and bootstrap command) when overriding env.
                path.write_text(json.dumps(template), encoding='utf-8')
                result = cli('containerapp', 'job', 'start', '-g', get('resource_group'),
                             '-n', get('job_name'), '--yaml', str(path))
            name = result['name']
            print('Azure execution started: ' + name, flush=True)
            deadline = time.monotonic() + 15000
            while time.monotonic() < deadline:
                execution = cli('containerapp', 'job', 'execution', 'show', '-g', get('resource_group'),
                                '-n', get('job_name'), '--job-execution-name', name)
                status = execution.get('properties', execution).get('status')
                if status == 'Succeeded':
                    print('Execution succeeded. Website: ' + get('website_url'))
                    return
                if status in ('Failed', 'Stopped', 'Degraded'):
                    raise RuntimeError('Azure execution failed: ' + str(status))
                print('Execution status: ' + str(status), flush=True)
                time.sleep(20)
            raise TimeoutError('Execution wait expired; inspect the Azure job before starting another run')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['upload', 'start', 'set-secret', 'check-ready', 'check-dependencies'])
    parser.add_argument('--outputs', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--mode', choices=['smoke', 'publish', 'collect'], default='smoke')
    parser.add_argument('--limit', type=int, default=40)
    try:
        main(parser.parse_args())
    except ModuleNotFoundError:
        print('Deployment Python dependency missing. Using the same Python selected with -Python, run: python -m pip install -r webapp/requirements-deploy.txt')
        raise SystemExit(1) from None
    except Exception as exc:
        # Azure SDK exception bodies can contain request details; never print them.
        print('Portfolio operation failed (' + type(exc).__name__ + '). Check execution status, RBAC and prerequisites.')
        raise SystemExit(1) from None
