"""Read-only catalog views over evidence-backed, append-only observations."""
import json
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
RETAILERS = {
    'kabum': {'name': 'KaBuM!', 'host': 'www.kabum.com.br'},
    'amazon': {'name': 'Amazon Brasil', 'host': 'www.amazon.com.br'},
    'americanas': {'name': 'Americanas', 'host': 'www.americanas.com.br'},
    'casasbahia': {'name': 'Casas Bahia', 'host': 'www.casasbahia.com.br'},
}


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS observations (
          product_id TEXT NOT NULL, retailer TEXT NOT NULL, observed_at TEXT NOT NULL,
          name TEXT NOT NULL, variant TEXT NOT NULL, price_cents INTEGER,
          currency TEXT, seller TEXT, availability TEXT, url TEXT NOT NULL,
          status TEXT NOT NULL, evidence TEXT NOT NULL, report_file TEXT NOT NULL,
          PRIMARY KEY (product_id, retailer, observed_at));
        CREATE TABLE IF NOT EXISTS runs (
          product_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempted_at TEXT NOT NULL,
          reason TEXT, image TEXT, image_status TEXT);
        ''')
        if 'source_method' not in {r['name'] for r in db.execute('PRAGMA table_info(observations)')}:
            db.execute("ALTER TABLE observations ADD COLUMN source_method TEXT NOT NULL DEFAULT 'agent'")


def import_report(db_path, product_id, report, report_file, retailer='kabum'):
    """No scraped text execution, network calls, fabricated history or zero prices."""
    if retailer not in RETAILERS:
        raise ValueError('Retailer must be configured explicitly')
    initialize(db_path)
    source_method = report.get('source_method', 'agent')
    if source_method not in ('agent', 'mcp_direct'):
        raise ValueError('Unknown collection method')
    fields, evidence = report.get('product') or {}, report.get('evidence') or {}
    if report.get('status') not in ('complete', 'partial'):
        return False
    for key in ('name', 'variant'):
        if not isinstance(fields.get(key), str) or not fields[key].strip() or not evidence.get(key):
            return False
    source = evidence['name']
    url = source.get('source_url', '')
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in (RETAILERS[retailer]['host'], RETAILERS[retailer]['host'].removeprefix('www.')) or parts.username or parts.password or parts.port not in (None,443):
        raise ValueError('Untrusted retailer source')
    observed = datetime.fromisoformat(source['observed_at'])
    if observed.tzinfo is None or observed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('Invalid observation time')
    timestamp = observed.astimezone(timezone.utc).isoformat()
    for key, value in fields.items():
        if value is not None:
            fact = evidence.get(key, {})
            if fact.get('source_url') != url or fact.get('observed_at') != source['observed_at']:
                raise ValueError('Mixed or missing evidence')
    cents = None
    if fields.get('price') is not None and fields.get('currency') == 'BRL':
        try:
            amount = Decimal(str(fields['price']))
            if not amount.is_finite() or amount <= 0 or amount > 100_000_000:
                raise ValueError('Invalid price')
            cents = int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        except InvalidOperation as exc:
            raise ValueError('Invalid canonical decimal price') from exc
    with connect(db_path) as db:
        identity = db.execute('SELECT variant FROM observations WHERE product_id=? LIMIT 1', (product_id,)).fetchone()
        if identity and identity['variant'] != fields['variant']:
            raise ValueError('Different retailer variant requires manual product mapping')
        existing = db.execute('SELECT variant,url FROM observations WHERE product_id=? AND retailer=? LIMIT 1', (product_id, retailer)).fetchone()
        if existing and (existing['variant'] != fields['variant'] or existing['url'] != url):
            raise ValueError('Changed variant/listing requires manual product mapping')
        db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (product_id,retailer,timestamp,fields['name'],fields['variant'],cents,
             fields.get('currency'),fields.get('seller'),fields.get('availability'),url,
             report['status'],json.dumps(evidence,ensure_ascii=False),str(report_file),source_method))
    return True


def record_run(db_path, product_id, status, reason=None, image=None, image_status=None):
    initialize(db_path)
    with connect(db_path) as db:
        db.execute('''INSERT INTO runs VALUES (?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET
          status=excluded.status,attempted_at=excluded.attempted_at,reason=excluded.reason,
          image=COALESCE(excluded.image,runs.image),image_status=CASE WHEN excluded.image IS NOT NULL OR runs.image IS NULL THEN excluded.image_status ELSE runs.image_status END''',
          (product_id,status,datetime.now(timezone.utc).isoformat(),reason,image,image_status))


def mean_cents(rows):
    usable = [r['price_cents'] for r in rows if r['price_cents'] is not None and r['currency']=='BRL' and r['availability'] in ('https://schema.org/InStock','http://schema.org/InStock','InStock')]
    return int((Decimal(sum(usable))/len(usable)).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if usable else None


class RetailCatalog:
    simulated = False

    def __init__(self, db_path=None, manifest=None, now=None):
        self.db_path = Path(db_path or ROOT/'data'/'catalog.sqlite3')
        self.manifest = manifest or ROOT/'data'/'products.json'
        self.now = now
        initialize(self.db_path)

    def list_products(self):
        now = self.now or datetime.now(timezone.utc)
        with connect(self.db_path) as db:
            observations = [dict(r) for r in db.execute('SELECT * FROM observations ORDER BY observed_at')]
            runs = {r['product_id']:dict(r) for r in db.execute('SELECT * FROM runs')}
        return build_products(json.loads(Path(self.manifest).read_text(encoding='utf-8')), observations, runs, now)

    def get_product(self, product_id):
        return next((p for p in self.list_products() if p['id']==product_id),None)


def build_products(manifest, observations, runs, now=None):
    now=now or datetime.now(timezone.utc)
    observations=sorted(observations, key=lambda r:r['observed_at'])
    result=[]
    for seed in manifest:
        rows=[r for r in observations if r['product_id']==seed['id']]
        latest={r['retailer']:r for r in rows}
        offers=[]
        for retailer, row in latest.items():
            offer={k:row[k] for k in ('price_cents','currency','seller','availability','url','observed_at','variant','status','source_method')}
            offer.update(retailer=RETAILERS.get(retailer, {'name':retailer})['name'],stale=now-datetime.fromisoformat(row['observed_at'])>timedelta(hours=48))
            offers.append(offer)
        current=mean_cents(offers)
        days={}
        for row in rows:
            days.setdefault(row['observed_at'][:10],{})[row['retailer']]=row
        history=[{'date':date,'price_cents':mean_cents(list(by_store.values())), 'retailer_count':sum(mean_cents([r]) is not None for r in by_store.values()),'event':None} for date,by_store in sorted(days.items())]
        history=[h for h in history if h['price_cents'] is not None]
        first=history[0] if history else None
        run=runs.get(seed['id'],{})
        last=rows[-1] if rows else {}
        result.append(dict(seed,name=last.get('name',seed['name']),variant=last.get('variant','Variante ainda não confirmada'),
            image=run.get('image'),image_status=run.get('image_status'),
            current_cents=current,first_observed_date=first['date'] if first else None,
            first_cents=first['price_cents'] if first else None,first_retailer_count=first['retailer_count'] if first else 0,
            change=round((current/first['price_cents']-1)*100,1) if current is not None and first else None,
            history=history,offers=offers,retailer_count=sum(mean_cents([o]) is not None for o in offers),
            oldest_current_at=min((o['observed_at'] for o in offers if mean_cents([o]) is not None),default=None),
            observed_at=last.get('observed_at'),status=run.get('status','pending'), reason=run.get('reason'),
            currency='BRL',source='Verificação direta MCP' if last.get('source_method')=='mcp_direct' else 'Agente de pesquisa',simulated=False))
    return result
