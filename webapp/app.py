"""Catálogo de ofertas observadas. Execute: python app.py."""
import io
import os
import unicodedata
from flask import Flask, jsonify, render_template, request, send_file
if __package__:
    from .cloud_catalog import AzureCatalog, StorageUnavailable
else:
    from cloud_catalog import AzureCatalog, StorageUnavailable
if __package__:
    from .retail_catalog import RetailCatalog
else:
    from retail_catalog import RetailCatalog


def create_app(repository=None):
    app = Flask(__name__)
    backend = os.environ.get('CATALOG_BACKEND', 'sqlite')
    if backend not in ('sqlite', 'azure'):
        raise ValueError('Unknown catalog backend')
    catalog = repository if repository is not None else (AzureCatalog() if backend == 'azure' else RetailCatalog())
    app.config.update(MAX_CONTENT_LENGTH=16384, SEND_FILE_MAX_AGE_DEFAULT=3600)
    app.config['SUGGESTION_EMAIL'] = os.environ.get('SUGGESTION_EMAIL', '')

    @app.template_filter('brl')
    def brl(value):
        return 'R$ ' + f'{value:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')

    @app.get('/')
    def index():
        items = catalog.list_products()
        retailers = sorted({offer['retailer'] for p in items for offer in p['offers']})
        return render_template('index.html', products=items, categories=sorted({p['category'] for p in items}), email=app.config['SUGGESTION_EMAIL'], verified=sum(p['current_cents'] is not None for p in items), retailers=retailers)

    @app.get('/api/products')
    def products():
        def normalize(text):
            return ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if not unicodedata.combining(c))
        query = normalize(request.args.get('q', '').strip())
        category = request.args.get('category', '')
        result = [p for p in catalog.list_products() if query in normalize(p['name'] + ' ' + p['category']) and (not category or category == p['category'])]
        return jsonify(products=result, simulated=False, price_method='Arithmetic mean of latest in-stock BRL reading per retailer; baseline is the earliest observed UTC day with a valid mean')

    @app.get('/api/products/<product_id>')
    def product(product_id):
        item = catalog.get_product(product_id)
        if item is None:
            return jsonify(error='Produto não encontrado'), 404
        return jsonify(product=item, simulated=False)

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/readyz')
    def ready():
        catalog.list_products()
        return jsonify(status='ready')

    @app.get('/media/<product_id>')
    def media(product_id):
        result = catalog.image(product_id) if isinstance(catalog, AzureCatalog) else None
        if result is None:
            return jsonify(error='Imagem não encontrada'), 404
        data, etag = result
        return send_file(io.BytesIO(data), mimetype='image/png', etag=etag,
                         max_age=300, conditional=True)

    @app.errorhandler(StorageUnavailable)
    def unavailable(error):
        app.logger.warning('catalog_storage_unavailable')
        return jsonify(error='Catálogo temporariamente indisponível. Tente novamente em instantes.'), 503, {'Retry-After': '30'}

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if backend == 'azure':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        if request.path.startswith('/api/') or request.path in ('/', '/readyz'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    return app


app = create_app()
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '5000')), debug=False)
