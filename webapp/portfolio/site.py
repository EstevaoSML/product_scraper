"""Export the existing Flask page; only deliberately public view data leaves ADLS."""
from copy import deepcopy
import hashlib
import re
from pathlib import Path
from flask import render_template
from webapp.retail_catalog import ROOT, build_products


def render_site(state, read_image, email=''):
    from webapp.app import create_app
    items = build_products(state['manifest'], state['observations'], state['runs'])
    files = {}
    for item in items:
        # Reasons may include scraper diagnostics; never publish private reports.
        item['reason'] = None
        item['image'] = None
        key = state['runs'].get(item['id'], {}).get('image_blob', '')
        if re.fullmatch(r'images/[a-z0-9-]+/[a-f0-9]{64}\.png', key):
            data = read_image(key)
            if len(data) > 10 * 1024 * 1024 or not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Invalid public image')
            if hashlib.sha256(data).hexdigest() != Path(key).stem:
                raise ValueError('Image hash mismatch')
            files[key] = data
            item['image'] = '/' + key
    # Hash assets so index.html is committed last without mixed JS/CSS releases.
    assets = {}
    for name in ('app.js', 'style.css'):
        data = (ROOT / 'static' / name).read_bytes()
        key = 'assets/' + hashlib.sha256(data).hexdigest() + '/' + name
        assets[name] = '/' + key
        files[key] = data
    class View:
        def list_products(self):
            return deepcopy(items)
    app = create_app(View())
    with app.test_request_context('/'):
        html = render_template('index.html', products=items,
                               categories=sorted({p['category'] for p in items}), email=email,
                               verified=sum(p['current_cents'] is not None for p in items),
                               retailers=sorted({o['retailer'] for p in items for o in p['offers']}))
    for name, url in assets.items():
        html = html.replace('/static/' + name, url)
    # Blob static hosting cannot set HTTP security headers. Meta CSP covers the
    # supported directives; frame-ancestors/HSTS require a paid edge or other host.
    csp = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    html = html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="' + csp + '"><meta name="referrer" content="strict-origin-when-cross-origin">')
    files['404.html'] = b'<!doctype html><html lang="en"><title>Not found</title><a href="/">Open catalog</a></html>'
    files['index.html'] = html.encode('utf-8')
    return files


def publish_site(container, state, private_container, lease, lost, email=''):
    from azure.storage.blob import ContentSettings
    from azure.core.exceptions import ResourceExistsError
    def read_image(key):
        blob = private_container.get_blob_client(key)
        if blob.get_blob_properties().size > 10 * 1024 * 1024:
            raise ValueError('Image too large')
        return blob.download_blob().readall()
    files = render_site(state, read_image, email)
    types = {'.html':'text/html; charset=utf-8', '.css':'text/css; charset=utf-8', '.js':'application/javascript; charset=utf-8', '.png':'image/png'}
    for name, data in files.items():
        if lost.is_set():
            raise RuntimeError('Writer lease lost')
        lease.renew()
        mutable = name.endswith('.html')
        try:
            container.upload_blob(name, data, overwrite=mutable,
                content_settings=ContentSettings(content_type=types[Path(name).suffix],
                    cache_control='no-cache' if mutable else 'public,max-age=31536000,immutable'))
        except ResourceExistsError:
            if mutable:
                raise
