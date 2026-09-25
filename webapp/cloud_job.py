"""Seed or collect into ADLS, serializing writers with a renewable blob lease."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid
if __package__:
    from .cloud_catalog import container_client, export_state, validate_state
else:
    from cloud_catalog import container_client, export_state, validate_state
if __package__:
    from .retail_catalog import ROOT, RetailCatalog, connect, import_report, record_run
else:
    from retail_catalog import ROOT, RetailCatalog, connect, import_report, record_run


@contextmanager
def writer(container):
    from azure.core.exceptions import ResourceExistsError
    blob = container.get_blob_client('catalog/latest.json')
    try:
        blob.upload_blob(b'{}', overwrite=False)
    except ResourceExistsError:
        pass
    lease = blob.acquire_lease(lease_duration=60)
    stopped, lost = threading.Event(), threading.Event()

    def renew():
        while not stopped.wait(15):
            try:
                lease.renew()
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield blob, lease, lost
    finally:
        stopped.set()
        thread.join(timeout=20)
        try:
            lease.release()
        except Exception:
            pass  # A crashed writer's lease also expires automatically.


def upload_file(container, key, path):
    from azure.core.exceptions import ResourceExistsError
    try:
        with path.open('rb') as data:
            container.upload_blob(key, data, overwrite=False)
    except ResourceExistsError:
        pass  # Immutable content, hash or unique batch in its path.


def publish(container, blob, lease, lost, state):
    if lost.is_set():
        raise RuntimeError('Writer lease lost')
    payload = json.dumps(validate_state(state), ensure_ascii=False).encode()
    lease.renew()
    container.upload_blob(f'catalog/snapshots/{uuid.uuid4().hex}.json', payload, overwrite=False)
    # Azure rejects this commit if this writer no longer owns the lease.
    blob.upload_blob(payload, overwrite=True, lease=lease)


def add_image(container, state, product_id, source):
    data = source.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 10 * 1024 * 1024:
        raise ValueError('Invalid PNG')
    key = f'images/{product_id}/{hashlib.sha256(data).hexdigest()}.png'
    upload_file(container, key, source)
    state['runs'][product_id]['image_blob'] = key


def restore_database(state, folder):
    manifest = folder / 'products.json'
    manifest.write_text(json.dumps(state['manifest']), encoding='utf-8')
    repo = RetailCatalog(folder / 'catalog.sqlite3', manifest)
    with connect(repo.db_path) as db:
        for row in state['observations']:
            columns = [r['name'] for r in db.execute('PRAGMA table_info(observations)')]
            db.execute('INSERT INTO observations (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', [row[c] for c in columns])
        for row in state['runs'].values():
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', [row.get(c) for c in ('product_id','status','attempted_at','reason','image','image_status')])
    return repo


def collect(container, blob, lease, lost, state, args):
    if not os.environ.get('OPENAI_API_KEY') or not os.environ.get('MCP_API_KEY'):
        raise ValueError('Missing Key Vault credentials')
    batch = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='catalog-') as scratch:
        folder = Path(scratch)
        repo = restore_database(state, folder)
        for seed in state['manifest']:
            if lost.is_set():
                raise RuntimeError('Writer lease lost')
            directory = folder / seed['id']
            directory.mkdir()
            env = os.environ.copy()
            env.pop('PYTHONPATH', None)
            env.pop('RESEARCH_STORAGE_ACCOUNT', None)
            command = [sys.executable, '-m', 'app.research_job', '--url', 'https://www.kabum.com.br/',
                       '--product', seed['query'], '--max-cost-usd', str(args.max_cost_usd),
                       '--image-max-cost-usd', str(args.image_max_cost_usd),
                       '--output-dir', str(directory), '--scrape-output-dir', str(directory / 'scrapes')]
            # Secret values stay in the environment, never arguments or logs.
            process = subprocess.Popen(command, cwd='/agent', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(60):
                    try:
                        code = process.wait(timeout=5)
                        break
                    except subprocess.TimeoutExpired:
                        if lost.is_set():
                            raise RuntimeError('Writer lease lost')
                else:
                    raise TimeoutError('Agent process timed out')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
            # Preserve diagnostics even when the agent failed; never expose to web readers.
            for path in directory.rglob('*'):
                if path.is_file() and path.suffix in ('.json', '.png'):
                    upload_file(container, f'reports/{batch}/{seed["id"]}/{path.relative_to(directory).as_posix()}', path)
            reports, diagnostics = sorted(directory.glob('research-*.json')), sorted(directory.glob('diagnostics-*.json'))
            if code or not reports or not diagnostics:
                raise RuntimeError('Agent failed; artifacts retained in Data Lake')
            report = json.loads(reports[-1].read_text(encoding='utf-8'))
            metadata = json.loads(diagnostics[-1].read_text(encoding='utf-8'))
            imported = import_report(repo.db_path, seed['id'], report, f'reports/{batch}/{seed["id"]}/{reports[-1].name}')
            image = metadata.get('image') or {}
            record_run(repo.db_path, seed['id'], report['status'], report.get('reason'), image_status=image.get('status'))
            updated = export_state(repo)
            for product_id, run in updated['runs'].items():
                if state['runs'].get(product_id, {}).get('image_blob'):
                    run['image_blob'] = state['runs'][product_id]['image_blob']
            if image.get('status') == 'generated' and image.get('file'):
                source = Path(image['file']).resolve()
                if not source.is_relative_to(directory.resolve()):
                    raise ValueError('Image outside output folder')
                add_image(container, updated, seed['id'], source)
            publish(container, blob, lease, lost, updated)
            state = updated
            print(json.dumps(dict(product_id=seed['id'], status=report['status'], imported=imported,
                                  research_cost=metadata.get('budgets', {}).get('usd_accounted'),
                                  image_cost=image.get('usd_accounted'))), flush=True)
            if report['status'] == 'blocked' or image.get('budget_exceeded'):
                raise RuntimeError('Collection stopped by agent control')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['seed', 'collect'])
    parser.add_argument('--max-cost-usd', type=float, default=.05)
    parser.add_argument('--image-max-cost-usd', type=float, default=0)
    args = parser.parse_args()
    if not 0 < args.max_cost_usd <= .05 or not 0 <= args.image_max_cost_usd <= .05:
        raise ValueError('Budget must be between 0 and USD 0.05 per product')
    container = container_client()
    with writer(container) as (blob, lease, lost):
        current = json.loads(blob.download_blob(lease=lease).readall())
        if args.action == 'seed':
            if current.get('schema_version'):
                validate_state(current)
                print('Catalog already initialized; no changes.')
                return
            state = export_state(RetailCatalog())
            for product_id, run in state['runs'].items():
                if run.get('image'):
                    source = (ROOT / run['image'].lstrip('/')).resolve()
                    if not source.is_relative_to((ROOT / 'static/products').resolve()):
                        raise ValueError('Invalid seed image path')
                    add_image(container, state, product_id, source)
            for path in (ROOT / 'data/reports').rglob('*'):
                if path.is_file() and path.suffix in ('.json', '.png'):
                    upload_file(container, 'reports/seed/' + path.relative_to(ROOT / 'data/reports').as_posix(), path)
            publish(container, blob, lease, lost, state)
            print('Seed catalog, images and evidence published.')
        else:
            collect(container, blob, lease, lost, validate_state(current), args)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # SDK messages may contain URLs or request contents. Log only a fixed code.
        print('catalog_job_failed; inspect execution and private artifacts', file=sys.stderr)
        sys.exit(1)
