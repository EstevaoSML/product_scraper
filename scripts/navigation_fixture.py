"""Real Chrome action check, executed inside the isolated CI browser container.

Uses a public HTTPS origin and a fixed synthetic DOM; never disables URL policy.
No LLM, user credentials, or retailer content is involved.
"""
import tempfile
from app.browser import create_driver
from app.navigation_browser import BrowserSession, NavigationError

FIXTURE = '''<h1>Retail fixture</h1>
<form action="https://example.com/" method="get"><input type="search" name="q" aria-label="Search products"></form>
<form action="https://example.com/checkout"><input type="password"><input name="search"></form>
<a href="https://example.com/?product=ps5">PS5 Digital console</a>
<a href="https://example.com/checkout">Checkout</a>
<a href="https://169.254.169.254/">Metadata</a>
<script type="application/ld+json">{"@type":"Product","name":"PS5 Digital","offers":{"price":"3000","priceCurrency":"BRL"}}</script>
'''

with tempfile.TemporaryDirectory(prefix='navigation-fixture-') as directory:
    driver = create_driver(directory)
    try:
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(10)
        session = BrowserSession(driver)
        session.execute('open_page', {'url': 'https://example.com/'})
        driver.execute_script('document.body.innerHTML=arguments[0]', FIXTURE)
        page = session.snapshot()
        assert page['products'][0]['name'] == 'PS5 Digital'
        assert len(page['elements']) == 2, page['elements']
        search = next(e for e in page['elements'] if e['action'] == 'search')
        found = session.execute('search_site', {'snapshot_id': page['snapshot_id'], 'element_id': search['element_id'], 'query': 'PS5'})
        assert 'q=PS5' in found['url']
        try:
            session.execute('search_site', {'snapshot_id': page['snapshot_id'], 'element_id': search['element_id'], 'query': 'PS5'})
            raise AssertionError('Old snapshot accepted')
        except NavigationError as exc:
            assert exc.code == 'stale_snapshot'
        driver.execute_script('document.body.innerHTML=arguments[0]', FIXTURE)
        page = session.snapshot()
        link = next(e for e in page['elements'] if e['action'] == 'follow_link')
        found = session.execute('follow_link', {'snapshot_id': page['snapshot_id'], 'element_id': link['element_id']})
        assert 'product=ps5' in found['url']
    finally:
        driver.quit()
print('Real Chrome navigation fixture passed')
