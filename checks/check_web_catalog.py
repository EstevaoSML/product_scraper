import uuid
from pathlib import Path
import unittest
from datetime import datetime, timezone
from webapp.app import create_app
from webapp.retail_catalog import RetailCatalog, import_report, record_run, RETAILERS


def report(price='100.01', date='2026-01-02T12:00:00+00:00', url='https://www.kabum.com.br/produto/123/test', variant='Exact model', availability='https://schema.org/InStock'):
    product=dict(name='Produto de teste',variant=variant,price=price,currency='BRL',seller=None,availability=availability)
    evidence={key:dict(quote=str(value),source_url=url,observed_at=date) for key,value in product.items() if value is not None}
    return dict(status='partial',product=product,evidence=evidence,reason=None)


class TestWebCatalog:
    assertEqual = staticmethod(unittest.TestCase().assertEqual)
    assertIn = staticmethod(unittest.TestCase().assertIn)
    assertNotIn = staticmethod(unittest.TestCase().assertNotIn)
    assertIsNone = staticmethod(unittest.TestCase().assertIsNone)
    assertTrue = staticmethod(unittest.TestCase().assertTrue)
    assertFalse = staticmethod(unittest.TestCase().assertFalse)
    assertRaises = staticmethod(unittest.TestCase().assertRaises)
    def setup_method(self):
        scratch=Path(__file__).resolve().parents[1]/'.ci-runtime'/'webapp-qa'
        scratch.mkdir(parents=True,exist_ok=True)
        self.db=scratch/(uuid.uuid4().hex+'.sqlite')
        self.repo=RetailCatalog(self.db,now=datetime(2026,1,2,13,tzinfo=timezone.utc))
        self.client=create_app(self.repo).test_client()

    def teardown_method(self):
        self.db.unlink(missing_ok=True)

    def add(self, r=None, retailer='kabum'):
        return import_report(self.db,'ps5-digital',r or report(),'fixture.json',retailer)

    def check_home_has_five_products_and_no_fake_prices(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.text.count('class="product-card"'),5)
        self.assertNotIn('Sem registro',response.text)
        self.assertNotIn('Há 1 ano',response.text)
        self.assertNotIn('R$ 2.499',response.text)

    def check_partial_report_with_missing_seller_keeps_price(self):
        self.assertTrue(self.add())
        p=self.repo.get_product('ps5-digital')
        self.assertEqual(p['current_cents'],10001)
        self.assertIsNone(p['offers'][0]['seller'])
        self.assertEqual(p['first_cents'],10001)
        self.assertEqual(p['first_observed_date'],'2026-01-02')
        self.assertEqual(p['change'],0)
        self.assertEqual(len(p['history']),1)
        page=self.client.get('/')
        self.assertEqual(page.status_code,200)
        self.assertIn('R$ 100,01',page.text)
        self.assertIn('02/01/2026',page.text)

    def check_duplicate_import_is_idempotent(self):
        self.add();self.add()
        self.assertEqual(len(self.repo.get_product('ps5-digital')['history']),1)

    def check_direct_mcp_provenance_is_preserved(self):
        data=report();data['source_method']='mcp_direct'
        self.add(data)
        p=self.repo.get_product('ps5-digital')
        self.assertEqual(p['offers'][0]['source_method'],'mcp_direct')
        self.assertEqual(p['source'],'Verificação direta MCP')
        data['source_method']='unknown'
        with self.assertRaises(ValueError):self.add(data)

    def check_research_retry_preserves_generated_image(self):
        record_run(self.db,'ps5-digital','partial',image='/static/products/ps5-digital.png',image_status='generated')
        record_run(self.db,'ps5-digital','running')
        record_run(self.db,'ps5-digital','partial',image_status='disabled')
        p=self.repo.get_product('ps5-digital')
        self.assertEqual(p['image'],'/static/products/ps5-digital.png')
        self.assertEqual(p['image_status'],'generated')

    def check_out_of_stock_excluded_from_mean(self):
        self.add(report(availability='https://schema.org/OutOfStock'))
        p=self.repo.get_product('ps5-digital')
        self.assertIsNone(p['current_cents'])
        self.assertEqual(p['offers'][0]['price_cents'],10001)

    def check_latest_price_kept_with_stale_flag(self):
        self.add(report(date='2025-12-29T12:00:00+00:00'))
        p=self.repo.get_product('ps5-digital')
        self.assertEqual(p['current_cents'],10001);self.assertTrue(p['offers'][0]['stale'])

    def check_comparison_uses_earliest_available_day(self):
        self.add(report('200',date='2025-03-12T12:00:00+00:00'))
        self.add(report('100'))
        p=self.repo.get_product('ps5-digital')
        self.assertEqual(p['first_cents'],20000)
        self.assertEqual(p['change'],-50)

    def check_mean_uses_one_latest_observation_per_retailer(self):
        RETAILERS['example']={'name':'Teste','host':'retailer.example'}
        try:
            self.add(report('100'))
            self.add(report('120',date='2026-01-02T12:30:00+00:00'))
            self.add(report('200',url='https://retailer.example/item'),retailer='example')
            p=self.repo.get_product('ps5-digital')
            self.assertEqual(p['current_cents'],16000)
            self.assertEqual(p['retailer_count'],2)
            self.assertEqual(p['first_cents'],16000)
            self.assertEqual(p['first_retailer_count'],2)
            self.assertEqual(len(p['offers']),2)
        finally:del RETAILERS['example']

    def check_reject_mixed_evidence_variant_and_unsafe_links(self):
        bad=report();bad['evidence']['price']['source_url']='https://other.example/'
        with self.assertRaises(ValueError):self.add(bad)
        for url in ('javascript:alert(1)','https://kabum.com.br.evil.example/a','https://user:secret@www.kabum.com.br/a','http://www.kabum.com.br/a'):
            with self.assertRaises(ValueError):self.add(report(url=url))
        self.add()
        with self.assertRaises(ValueError):self.add(report(variant='Other edition'))

    def check_null_and_invalid_price(self):
        self.add(report(price=None))
        self.assertIsNone(self.repo.get_product('ps5-digital')['current_cents'])
        for value in ('NaN','-1','0','1.299,99','Infinity'):
            with self.assertRaises(ValueError):self.add(report(value))

    def check_search_and_detail(self):
        self.assertEqual(len(self.client.get('/api/products?q=audio').json['products']),1)
        self.assertEqual(len(self.client.get('/api/products?category=Periféricos').json['products']),2)
        self.assertEqual(self.client.get('/api/products?category=Áudio&q=ps5').json['products'],[])
        self.assertFalse(self.client.get('/api/products').json['simulated'])
        self.assertEqual(self.client.get('/api/products/missing').status_code,404)



