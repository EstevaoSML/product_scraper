"""Stdlib-only ACA entrypoint. Terraform embeds this trusted bootstrap, not a URL script."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import zipfile
import uuid
from datetime import datetime, timezone

MAX_PACKAGE = 512 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused')


def safe_extract(archive, target):
    target = Path(target).resolve()
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 30000 or sum(m.file_size for m in members) > 1536 * 1024 * 1024:
            raise ValueError('Archive exceeds limit')
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (path.is_absolute() or '..' in path.parts or '\\' in member.filename
                    or ':' in member.filename or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                raise ValueError('Unsafe archive member')
            destination = target.joinpath(*path.parts)
            if not destination.resolve().is_relative_to(target):
                raise ValueError('Archive traversal')
        for member in members:
            destination = Path(source.extract(member, target))
            if not member.is_dir():
                destination.chmod(0o755 if member.external_attr >> 16 & 0o111 else 0o644)


def storage_access(timeout=30):
    account = os.environ['CATALOG_STORAGE_ACCOUNT']
    digest = os.environ['PACKAGE_SHA256']
    client_id = os.environ['AZURE_CLIENT_ID']
    if not re.fullmatch(r'[a-z0-9]{3,24}', account) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid artifact identifier')
    # This endpoint is injected by Azure, never supplied by scraped content.
    endpoint = os.environ['IDENTITY_ENDPOINT']
    parts = urlsplit(endpoint)
    if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost') or parts.query or parts.fragment or parts.username:
        raise ValueError('Invalid managed identity endpoint')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    query = urlencode(dict(resource='https://storage.azure.com/', **{'api-version': '2019-08-01'}, client_id=client_id))
    with opener.open(Request(endpoint + '?' + query, headers={'X-IDENTITY-HEADER': os.environ['IDENTITY_HEADER']}), timeout=timeout) as response:
        token = json.loads(response.read(65536))['access_token']
    return opener, account, digest, token


def download_package(destination):
    opener, account, digest, token = storage_access()
    request = Request(f'https://{account}.blob.core.windows.net/packages/{digest}.zip',
                      headers={'Authorization': 'Bearer ' + token, 'x-ms-version': '2023-11-03'})
    total, actual = 0, hashlib.sha256()
    with opener.open(request, timeout=60) as response, destination.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_PACKAGE:
                raise ValueError('Package too large')
            actual.update(chunk)
            output.write(chunk)
    if actual.hexdigest() != digest:
        raise ValueError('Package digest mismatch')


STAGES = frozenset(('bootstrap', 'package_download', 'package_extract', 'browser_extract',
                    'venv_create', 'dependency_install', 'worker_start', 'worker_import',
                    'catalog_open', 'browser_smoke', 'collection', 'site_publish', 'complete'))
CURRENT_STAGE = 'bootstrap'


def diagnostic(stage, error=None):
    """Best-effort bounded records: no exception text, command output or secrets."""
    global CURRENT_STAGE
    if stage not in STAGES:
        raise ValueError('Unknown diagnostic stage')
    CURRENT_STAGE = stage
    record = dict(stage=stage, status='failed' if error is not None else ('succeeded' if stage == 'complete' else 'running'),
                  at=datetime.now(timezone.utc).isoformat())
    if error is not None:
        record['error_type'] = type(error).__name__ if type(error).__module__ in ('builtins', 'subprocess', 'urllib.error') else 'ApplicationError'
        for attr in ('returncode', 'code'):
            value = getattr(error, attr, None)
            if type(value) is int:
                record[attr] = value
    print(json.dumps(record), flush=True)
    try:
        run_id = os.environ.setdefault('PORTFOLIO_DIAGNOSTIC_ID', uuid.uuid4().hex)
        if not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise ValueError('Invalid diagnostic ID')
        opener, account, digest, token = storage_access(timeout=5)
        record['package_sha256'] = digest
        record['run_id'] = run_id
        request = Request(f'https://{account}.blob.core.windows.net/catalog/executions/diagnostics/{run_id}.json',
                          data=json.dumps(record).encode('utf-8'), method='PUT',
                          headers={'Authorization': 'Bearer ' + token, 'x-ms-version': '2023-11-03',
                                   'x-ms-blob-type': 'BlockBlob', 'Content-Type': 'application/json'})
        with opener.open(request, timeout=5):
            pass
    except Exception:
        print('diagnostic_storage_unavailable', flush=True)


def create_environment(directory):
    # Playwright Python Noble ships virtualenv and uses it in its own build.
    # Ubuntu's stdlib venv can fail when optional ensurepip is absent.
    diagnostic('venv_create')
    subprocess.run([sys.executable, '-m', 'virtualenv', '--no-download',
                    '--no-periodic-update', str(directory / 'venv')],
                   check=True, timeout=90)
    return str(directory / 'venv/bin/python')


def main():
    with tempfile.TemporaryDirectory(prefix='portfolio-') as scratch:
        directory = Path(scratch)
        archive = directory / 'package.zip'
        diagnostic('package_download')
        download_package(archive)
        root = directory / 'app'
        diagnostic('package_extract')
        safe_extract(archive, root)
        archive.unlink()
        diagnostic('browser_extract')
        for name in ('chrome', 'chromedriver'):
            safe_extract(root / 'browser' / (name + '.zip'), root / 'browser')
        # A venv avoids changing the public MCR base. Dependencies are pinned by
        # the existing constraints plus the portfolio requirements.
        python = create_environment(directory)
        diagnostic('dependency_install')
        subprocess.run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir',
                        '-r', 'webapp/portfolio/requirements.txt'], cwd=root, check=True, timeout=600,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        version = json.loads((root / 'browser/manifest.json').read_text())['version']
        env = dict(os.environ, CHROME_BINARY=str(root / 'browser/chrome-linux64/chrome'),
                   CHROMEDRIVER_PATH=str(root / 'browser/chromedriver-linux64/chromedriver'), CHROME_MAJOR=version.split('.')[0])
        # exec ensures ACA cancellation reaches the collector, which owns children.
        diagnostic('worker_start')
        os.chdir(root)
        os.execve(python, [python, '-m', 'webapp.portfolio.worker'], env)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        diagnostic(CURRENT_STAGE, exc)
        print('portfolio_bootstrap_failed', file=sys.stderr)
        sys.exit(1)
