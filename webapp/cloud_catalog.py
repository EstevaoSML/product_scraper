"""Private ADLS Gen2 blobs, accessed with managed identity (never account keys)."""
import json
import os
import re
import threading
import time
if __package__:
    from .retail_catalog import build_products, connect
else:
    from retail_catalog import build_products, connect


class StorageUnavailable(RuntimeError):
    pass


def container_client():
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    account = os.environ['CATALOG_STORAGE_ACCOUNT']
    if not re.fullmatch(r'[a-z0-9]{3,24}', account):
        raise ValueError('Invalid storage account')
    credential = ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    return BlobServiceClient(
        f'https://{account}.blob.core.windows.net', credential=credential,
        connection_timeout=5, read_timeout=10, retry_total=1,
        logging_enable=False).get_container_client('catalog')


def export_state(repository):
    with connect(repository.db_path) as db:
        return dict(schema_version=1,
                    manifest=json.loads(repository.manifest.read_text(encoding='utf-8')),
                    observations=[dict(r) for r in db.execute('SELECT * FROM observations ORDER BY observed_at')],
                    runs={r['product_id']: dict(r) for r in db.execute('SELECT * FROM runs')})


def validate_state(state):
    if (state.get('schema_version') != 1 or not isinstance(state.get('manifest'), list)
            or not isinstance(state.get('observations'), list) or not isinstance(state.get('runs'), dict)):
        raise ValueError('Invalid catalog schema')
    ids = [p['id'] for p in state['manifest']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-z0-9-]{1,80}', i) for i in ids):
        raise ValueError('Invalid product IDs')
    return state


class AzureCatalog:
    """Short process-local cache; storage failure is explicit, never fake data."""
    def __init__(self, container=None, ttl=30):
        self.container = container if container is not None else container_client()
        self.ttl = ttl
        self._state = None
        self._expires = 0
        self._lock = threading.Lock()

    def state(self):
        with self._lock:
            if self._state is not None and time.monotonic() < self._expires:
                return self._state
            try:
                blob = self.container.get_blob_client('catalog/latest.json')
                if blob.get_blob_properties().size > 32 * 1024 * 1024:
                    raise ValueError('Catalog too large')
                state = validate_state(json.loads(blob.download_blob(max_concurrency=1).readall()))
            except Exception as exc:
                raise StorageUnavailable('Catalog temporarily unavailable') from exc
            self._state = state
            self._expires = time.monotonic() + self.ttl
            return state

    def list_products(self):
        state = self.state()
        items = build_products(state['manifest'], state['observations'], state['runs'])
        for item in items:
            item['image'] = f"/media/{item['id']}" if state['runs'].get(item['id'], {}).get('image_blob') else None
        return items

    def get_product(self, product_id):
        return next((p for p in self.list_products() if p['id'] == product_id), None)

    def image(self, product_id):
        state = self.state()
        if product_id not in {p['id'] for p in state['manifest']}:
            return None
        key = state['runs'].get(product_id, {}).get('image_blob', '')
        if not re.fullmatch(r'images/[a-z0-9-]+/[a-f0-9]{64}\.png', key):
            return None
        try:
            blob = self.container.get_blob_client(key)
            props = blob.get_blob_properties()
            if props.size > 10 * 1024 * 1024:
                raise ValueError('Image too large')
            data = blob.download_blob(max_concurrency=1).readall()
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Invalid image')
            return data, key.rsplit('/', 1)[-1].removesuffix('.png')
        except Exception as exc:
            raise StorageUnavailable('Image temporarily unavailable') from exc
