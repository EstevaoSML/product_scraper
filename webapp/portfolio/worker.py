"""One Azure job, one browser, at most 40 model attempts / USD 2 reserved per UTC month."""
from webapp.portfolio import bootstrap as job_diagnostics
from webapp.portfolio.bootstrap import diagnostic
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from webapp.cloud_catalog import export_state, validate_state
from webapp.cloud_job import add_image, publish, restore_database, upload_file, writer
from webapp.retail_catalog import ROOT, RETAILERS, RetailCatalog, import_report, record_run
from .site import publish_site

RESERVATION_CENTS = 5
MONTHLY_CENTS = 200
MAX_ATTEMPTS = 40


def reserve(ledger, month, product_id, retailer):
    """Fail closed on corrupt ledgers; a crash does not refund a reservation."""
    if ledger.get('month') != month or ledger.get('schema_version') != 1:
        raise ValueError('Invalid monthly ledger')
    attempts = ledger.get('attempts')
    if not isinstance(attempts, dict) or len(attempts) > MAX_ATTEMPTS or any(a.get('reserved_cents') != RESERVATION_CENTS for a in attempts.values()):
        raise ValueError('Invalid reservations')
    key = product_id + '/' + retailer
    if key in attempts or (len(attempts) + 1) * RESERVATION_CENTS > MONTHLY_CENTS:
        return None
    attempts[key] = {'reserved_cents': RESERVATION_CENTS, 'status': 'reserved',
                     'at': datetime.now(timezone.utc).isoformat()}
    return key


def child_environment(source):
    # Do not inherit Azure identity endpoints, API keys, proxy variables, Python
    # injection settings, or storage credentials into browser/MCP subprocesses.
    allowed = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'CHROME_BINARY', 'CHROMEDRIVER_PATH', 'CHROME_MAJOR')
    return {key: source[key] for key in allowed if key in source}


def stop_process(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@contextmanager
def services():
    import httpx
    processes = []
    mcp_key, browser_key = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    base = child_environment(os.environ)
    browser_env = dict(base, API_KEY=browser_key, BROWSER_PROXY='http://127.0.0.1:8080')
    api_env = dict(base, API_KEY=mcp_key, BROWSER_API_KEY=browser_key, BROWSER_URL='http://127.0.0.1:8001')
    try:
        for command, env in [([sys.executable, '-m', 'app.egress'], base),
            ([sys.executable, '-m', 'uvicorn', 'app.browser_worker:app', '--host', '127.0.0.1', '--port', '8001', '--no-access-log'], browser_env),
            ([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--no-access-log'], api_env)]:
            processes.append(subprocess.Popen(command, env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        deadline = time.monotonic() + 45
        with httpx.Client(trust_env=False, timeout=2) as client:
            while True:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError('Local services failed')
                try:
                    client.get('http://127.0.0.1:8000/readyz').raise_for_status()
                    break
                except httpx.HTTPError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Local services not ready') from None
                    time.sleep(.5)
        yield mcp_key
    finally:
        for process in reversed(processes):
            stop_process(process)


def run_agent(seed, retailer, directory, api_key, lost):
    with services() as mcp_key:
        env = dict(child_environment(os.environ), OPENAI_API_KEY=api_key, MCP_API_KEY=mcp_key,
                   MCP_ENDPOINT='http://127.0.0.1:8000/mcp')
        command = [sys.executable, '-m', 'app.research_job', '--url', 'https://' + RETAILERS[retailer]['host'] + '/',
                   '--product', seed['query'], '--max-cost-usd', '0.05', '--image-max-cost-usd', '0',
                   '--output-dir', str(directory), '--scrape-output-dir', str(directory / 'scrapes')]
        if lost.is_set():
            raise RuntimeError('Writer lease lost')
        process = subprocess.Popen(command, env=env, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 300
        try:
            while process.poll() is None:
                if lost.wait(1) or time.monotonic() >= deadline:
                    raise RuntimeError('Agent stopped by timeout or lost lease')
            return process.returncode
        finally:
            stop_process(process)


def browser_smoke():
    import httpx
    with services() as key, httpx.Client(trust_env=False, timeout=150) as client:
        response = client.post('http://127.0.0.1:8000/scrape', headers={'X-API-Key': key},
                               json={'url': 'https://example.com/', 'wait_seconds': 0})
        response.raise_for_status()
        result = response.json()
        if 'Example Domain' not in result.get('html', ''):
            raise RuntimeError('Browser smoke failed')
    print('browser_smoke_passed; no model calls', flush=True)


def seed_state(container):
    state = export_state(RetailCatalog())
    state['manifest'] = json.loads((Path(__file__).parent / 'products.json').read_text(encoding='utf-8'))
    for product_id, run in state['runs'].items():
        if run.get('image'):
            source = (ROOT / run['image'].lstrip('/')).resolve()
            if not source.is_relative_to((ROOT / 'static/products').resolve()):
                raise ValueError('Invalid seed image path')
            add_image(container, state, product_id, source)
    return state


def collect(container, blob, lease, lost, state, credential, limit):
    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceNotFoundError
    from azure.keyvault.secrets import SecretClient
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    ledger_blob = container.get_blob_client('ledger/' + month + '.json')
    try:
        download = ledger_blob.download_blob()
        ledger = json.loads(download.readall())
        etag = download.properties.etag
    except ResourceNotFoundError:
        ledger, etag = dict(schema_version=1, month=month, attempts={}), None
    def save_ledger():
        nonlocal etag
        if lost.is_set():
            raise RuntimeError('Writer lease lost')
        lease.renew()
        options = dict(overwrite=True, etag=etag, match_condition=MatchConditions.IfNotModified) if etag else dict(overwrite=False)
        result = ledger_blob.upload_blob(json.dumps(ledger).encode(), **options)
        etag = result['etag']
    # Read only this one secret and keep it out of Terraform state and browser env.
    vault = os.environ['KEY_VAULT_NAME']
    with SecretClient(f'https://{vault}.vault.azure.net', credential, logging_enable=False,
                      connection_timeout=5, read_timeout=10, retry_total=1) as client:
        api_key = client.get_secret('openai-api-key').value
    if not api_key:
        raise ValueError('Missing OpenAI secret')
    attempted = 0
    with tempfile.TemporaryDirectory(prefix='monthly-') as scratch:
        repo = restore_database(state, Path(scratch))
        for seed in state['manifest']:
            for retailer in RETAILERS:
                if attempted >= limit or datetime.now(timezone.utc).strftime('%Y-%m') != month:
                    return state
                key = reserve(ledger, month, seed['id'], retailer)
                if key is None:
                    continue
                save_ledger()  # Durable reservation MUST precede any paid process.
                attempted += 1
                directory = Path(scratch) / seed['id'] / retailer
                directory.mkdir(parents=True)
                status, accounted = 'failed', None
                report_prefix = f'reports/{month}/{uuid.uuid4().hex}/{seed["id"]}/{retailer}'
                try:
                    code = run_agent(seed, retailer, directory, api_key, lost)
                    reports = sorted(directory.glob('research-*.json'))
                    metadata = sorted(directory.glob('diagnostics-*.json'))
                    if code == 0 and reports and metadata:
                        report = json.loads(reports[-1].read_text(encoding='utf-8'))
                        diagnostics = json.loads(metadata[-1].read_text(encoding='utf-8'))
                        accounted = diagnostics.get('budgets', {}).get('usd_accounted')
                        imported = import_report(repo.db_path, seed['id'], report,
                                                 report_prefix + '/' + reports[-1].name, retailer=retailer)
                        status = 'imported' if imported else 'unconfirmed'
                except ValueError:
                    status = 'rejected_mapping_or_evidence'
                except RuntimeError:
                    status = 'failed'
                finally:
                    # Raw results stay private, including rejected variants.
                    for path in directory.rglob('*.json'):
                        if path.is_file() and not path.is_symlink():
                            upload_file(container, report_prefix + '/' + path.relative_to(directory).as_posix(), path)
                ledger['attempts'][key].update(status=status, accounted_usd=accounted)
                save_ledger()
                record_run(repo.db_path, seed['id'], status, reason=None)
                updated = export_state(repo)
                for product_id, run in updated['runs'].items():
                    old_key = state['runs'].get(product_id, {}).get('image_blob')
                    if old_key:
                        run['image_blob'] = old_key
                publish(container, blob, lease, lost, updated)
                state = updated
                print(json.dumps(dict(product=seed['id'], retailer=retailer, status=status)), flush=True)
    return state


def main():
    diagnostic('worker_import')
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    action = os.environ.get('RUN_MODE', 'smoke')
    limit = int(os.environ.get('MAX_TASKS', '40'))
    if action not in ('smoke', 'publish', 'collect') or not 1 <= limit <= MAX_ATTEMPTS:
        raise ValueError('Invalid job configuration')
    # Always use the configured identity, never developer credentials in Azure.
    credential = ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    service = BlobServiceClient('https://' + os.environ['CATALOG_STORAGE_ACCOUNT'] + '.blob.core.windows.net',
                               credential, connection_timeout=5, read_timeout=10, retry_total=1)
    diagnostic('catalog_open')
    with credential, service:
        container = service.get_container_client('catalog')
        with writer(container) as (blob, lease, lost):
            current = json.loads(blob.download_blob(lease=lease).readall())
            if current.get('schema_version'):
                state = validate_state(current)
            else:
                state = seed_state(container)
                publish(container, blob, lease, lost, state)
            if action == 'smoke':
                diagnostic('browser_smoke')
                browser_smoke()
            elif action == 'collect':
                diagnostic('collection')
                state = collect(container, blob, lease, lost, state, credential, limit)
            diagnostic('site_publish')
            publish_site(service.get_container_client('$web'), state, container, lease, lost,
                         os.environ.get('SUGGESTION_EMAIL', ''))
            if action == 'smoke':
                container.upload_blob('executions/smoke.json', json.dumps({
                    'package_sha256': os.environ['PACKAGE_SHA256'], 'status': 'passed',
                    'at': datetime.now(timezone.utc).isoformat()}), overwrite=True)
            diagnostic('complete')
            print('portfolio_published; action=' + action, flush=True)


if __name__ == '__main__':
    def cancelled(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        diagnostic(job_diagnostics.CURRENT_STAGE, exc)
        print('portfolio_job_failed; inspect execution status and private ledger', file=sys.stderr)
        sys.exit(1)
