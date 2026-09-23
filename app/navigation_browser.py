"""Isolated, line-oriented browser process. All JavaScript here is fixed application code."""
import base64
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from app.browser import create_driver
from app.policy import URLPolicyError, configured_hosts, validate_url_structure
from app.navigation import MAX_NAVIGATION_BYTES

MAX_SNAPSHOT = MAX_NAVIGATION_BYTES
FORBIDDEN = re.compile(r"(?:cart|checkout|basket|payment|purchase|logout|login|sign[-_]?out|delete|remove|subscribe|carrinho|pagamento|comprar|excluir)", re.I)
# Search eligibility is deliberately narrower than every editable input.
SEARCH_CHECK = """
const e=arguments[0];
if (!e || !e.matches('input') || e.disabled || e.readOnly) return false;
if (!['search','text',''].includes((e.getAttribute('type')||'').toLowerCase())) return false;
if (e.form && (e.form.querySelector('input[type=password]') || e.form.method.toLowerCase() !== 'get')) return false;
const label=[e.name,e.id,e.getAttribute('aria-label'),e.placeholder].join(' ');
return e.type==='search' || !!e.closest('[role=search]') || /(^|[\\s_-])(q|query|search|busca|buscar|pesquisa|pesquisar)([\\s_-]|$)/i.test(label);
"""
SNAPSHOT = """
const visible=e=>!!(e.getClientRects().length) && getComputedStyle(e).visibility!=='hidden';
const rows=[];
for (const e of document.querySelectorAll('input,a[href]')) {
  if (rows.length>=100) break;
  if (!visible(e)) continue;
  const tag=e.tagName.toLowerCase();
  if (tag==='input' && !['search','text'].includes(e.type)) continue;
  rows.push({node:e,tag,type:e.type||'',name:(e.name||'').slice(0,200),
    accessible_name:(e.getAttribute('aria-label')||e.innerText||e.getAttribute('title')||'').trim().slice(0,300),
    placeholder:(e.placeholder||'').slice(0,200),href:tag==='a'?e.href.slice(0,4096):null});
}
const heading=Array.from(document.querySelectorAll('h1')).find(visible);
const meta=(selector)=>document.querySelector(selector)?.content?.trim()||'';
const productName=(heading?.innerText||meta('meta[property="og:title"]')||document.title).trim().slice(0,1000);
const description=(meta('meta[name="description"]')||meta('meta[property="og:description"]')).slice(0,5000);
const imageCandidates=Array.from(document.images).filter(e=>visible(e)&&e.naturalWidth>=120&&e.naturalHeight>=120)
  .map(e=>({node:e,alt:(e.alt||'').trim().slice(0,1000),src:(e.currentSrc||e.src||'').slice(0,4096),
    width:e.naturalWidth,height:e.naturalHeight,
    score:Math.min(e.getBoundingClientRect().width*e.getBoundingClientRect().height,1000000)+
      ((e.alt||'').toLowerCase().split(/\\s+/).filter(w=>w.length>2&&productName.toLowerCase().includes(w)).length*100000)}))
  .sort((a,b)=>b.score-a.score);
return {title:document.title.slice(0,500),visible_text:(document.body?.innerText||'').slice(0,20000),
  text_truncated:(document.body?.innerText||'').length>20000, rows,
  jsonld:Array.from(document.querySelectorAll('script[type="application/ld+json"]')).slice(0,10).map(e=>e.textContent.slice(0,32000)),
  page_info:{product_name:productName,description},
  images:imageCandidates.slice(0,20)};
"""


def host_group(url):
    return (urlsplit(url).hostname or '').lower().rstrip('.').removeprefix('www.')


class NavigationError(Exception):
    def __init__(self, code):
        self.code = code


class BrowserSession:
    def __init__(self, driver):
        self.driver = driver
        self.retailer = None
        self.snapshot_id = None
        self.elements = {}
        self.images = {}
        self.page_url = None
        self.followed = 0
        self.allowed = configured_hosts(os.getenv('URL_POLICY', 'public'), os.getenv('ALLOWED_HOSTS', ''))

    def check_url(self, url):
        # Chrome has no direct Internet access. The mandatory egress proxy
        # resolves and pins public addresses for every connection and redirect.
        validate_url_structure(url, self.allowed)
        if self.retailer and host_group(url) != self.retailer:
            raise NavigationError('outside_retailer')
        parts = urlsplit(url)
        if FORBIDDEN.search(unquote(parts.path + '?' + parts.query)):
            raise NavigationError('action_not_allowed')

    def settle(self):
        WebDriverWait(self.driver, 20).until(lambda d: d.execute_script('return document.readyState') in ('interactive', 'complete'))
        time.sleep(1)  # Bounded rendering grace period; inspect_page can refresh later.
        self.check_url(self.driver.current_url)

    def snapshot(self):
        self.check_url(self.driver.current_url)
        raw = self.driver.execute_script(SNAPSHOT)
        page_info = raw.get('page_info') if isinstance(raw.get('page_info'), dict) else {}
        raw['page_info'] = page_info
        self.snapshot_id = uuid.uuid4().hex
        self.page_url = self.driver.current_url
        self.elements = {}
        self.images = {}
        images = []
        for row in raw.pop('images', []):
            if not isinstance(row, dict):
                continue
            node = row.pop('node', None)
            if node is None:
                continue
            ref = 'i' + str(len(images))
            row['image_id'] = ref
            self.images[ref] = (node, dict(row))
            images.append(row)
        elements = []
        for row in raw.pop('rows'):
            node = row.pop('node')
            if row['tag'] == 'input':
                if not self.driver.execute_script(SEARCH_CHECK, node):
                    continue
                row['action'] = 'search'
            else:
                try:
                    self.check_url(row['href'])
                except (ValueError, NavigationError):
                    continue
                row['action'] = 'follow_link'
            ref = 'e' + str(len(elements))
            row['element_id'] = ref
            self.elements[ref] = (node, dict(row))
            elements.append(row)
        products = []
        def collect(item, depth=0):
            if depth > 8 or len(products) >= 20:
                return
            if isinstance(item, list):
                for value in item[:100]:
                    collect(value, depth+1)
            if isinstance(item, dict):
                types = item.get('@type', [])
                if types == 'Product' or isinstance(types, list) and 'Product' in types:
                    products.append(item)
                else:
                    for value in list(item.values())[:100]:
                        collect(value, depth+1)
        for script in raw.pop('jsonld'):
            try:
                collect(json.loads(script))
            except (ValueError, RecursionError):
                pass
        if products:
            if not page_info.get('product_name') and isinstance(products[0].get('name'), str):
                page_info['product_name'] = products[0]['name'][:1000]
            if not page_info.get('description') and isinstance(products[0].get('description'), str):
                page_info['description'] = products[0]['description'][:5000]
        text = raw['visible_text'].lower()
        status = 'blocked' if any(x in text for x in ('verify you are human', 'access denied', 'verifique que você é humano')) else 'ok'
        result = {**raw, 'snapshot_id': self.snapshot_id, 'url': self.page_url, 'elements': elements,
                  'images': images, 'products': products, 'status': status, 'untrusted_content': True,
                  'fetched_at': datetime.now(timezone.utc).isoformat(), 'pages_remaining': 5-self.followed}
        if len(json.dumps(result).encode()) > MAX_SNAPSHOT:
            result['products'] = []
            result['structured_data_truncated'] = True
        return result

    def execute(self, action, arguments):
        if action == 'capture_image':
            if arguments['snapshot_id'] != self.snapshot_id or self.driver.current_url != self.page_url:
                raise NavigationError('stale_snapshot')
            if arguments['element_id'] not in self.images:
                raise NavigationError('unknown_element')
            node, observed = self.images[arguments['element_id']]
            if not node.is_displayed():
                raise NavigationError('stale_snapshot')
            current = self.driver.execute_script('return arguments[0].currentSrc || arguments[0].src || ""', node)
            if current != observed.get('src'):
                raise NavigationError('stale_snapshot')
            png = node.screenshot_as_png
            if not png:
                raise NavigationError('image_unavailable')
            return {'snapshot_id': self.snapshot_id, 'url': self.page_url,
                    'image_id': arguments['element_id'],
                    'image_base64': base64.b64encode(png).decode('ascii')}
        if action == 'open_page':
            self.check_url(arguments['url'])
            self.retailer = host_group(arguments['url'])
            self.driver.get(arguments['url'])
        elif action == 'inspect_page':
            pass
        elif action in ('search_site', 'follow_link'):
            if arguments['snapshot_id'] != self.snapshot_id or self.driver.current_url != self.page_url:
                raise NavigationError('stale_snapshot')
            if arguments['element_id'] not in self.elements:
                raise NavigationError('unknown_element')
            node, observed = self.elements[arguments['element_id']]
            if not node.is_displayed() or not node.is_enabled():
                raise NavigationError('stale_snapshot')
            self.check_url(self.driver.current_url)
            if action == 'search_site':
                if observed['action'] != 'search' or not self.driver.execute_script(SEARCH_CHECK, node):
                    raise NavigationError('action_not_allowed')
                form_url = self.driver.execute_script('return arguments[0].form?.action || location.href', node)
                self.check_url(form_url)
                node.clear()
                node.send_keys(arguments['query'])
                node.send_keys(Keys.ENTER)
            else:
                if observed['action'] != 'follow_link':
                    raise NavigationError('action_not_allowed')
                if node.get_attribute('href') != observed['href']:
                    raise NavigationError('stale_snapshot')
                self.check_url(observed['href'])
                if self.followed >= 5:
                    raise NavigationError('page_limit')
                self.followed += 1
                # Navigate the observed URL, never execute a site's arbitrary onclick handler.
                self.driver.get(observed['href'])
        else:
            raise NavigationError('unknown_action')
        self.settle()
        return self.snapshot()


def main():
    # Keep incidental UC output off the JSON protocol stream.
    output = sys.stdout
    sys.stdout = sys.stderr
    driver = create_driver(os.environ['BROWSER_JOB_DIR'])
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(10)
    session = BrowserSession(driver)
    try:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                result = session.execute(command['action'], command['arguments'])
            except NavigationError as exc:
                result = {'error': exc.code}
            except URLPolicyError:
                result = {'error': 'destination_rejected'}
            except StaleElementReferenceException:
                result = {'error': 'stale_snapshot'}
            except TimeoutException:
                result = {'error': 'browser_timeout'}
            except (WebDriverException, ValueError, KeyError, TypeError):
                result = {'error': 'browser_error'}
            output.write(json.dumps(result, ensure_ascii=True) + '\n')
            output.flush()
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
