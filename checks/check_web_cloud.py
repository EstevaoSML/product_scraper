import json
from types import SimpleNamespace
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock
from webapp.app import create_app
from webapp.cloud_catalog import AzureCatalog, StorageUnavailable, export_state
from webapp.cloud_job import restore_database, publish, collect
from checks.check_web_catalog import TestWebCatalog as CatalogSetup, report


class TestWebCloud:
    setup_method = CatalogSetup.setup_method
    teardown_method = CatalogSetup.teardown_method
    add = CatalogSetup.add
    assertEqual = staticmethod(unittest.TestCase().assertEqual)
    assertIs = staticmethod(unittest.TestCase().assertIs)
    assertIsNone = staticmethod(unittest.TestCase().assertIsNone)
    assertIn = staticmethod(unittest.TestCase().assertIn)
    assertNotIn = staticmethod(unittest.TestCase().assertNotIn)
    assertTrue = staticmethod(unittest.TestCase().assertTrue)
    assertRaises = staticmethod(unittest.TestCase().assertRaises)
    def check_roundtrip_preserves_observations(self):
        self.add(report('123.45'))
        state = export_state(self.repo)
        folder = self.db.parent / self.db.stem
        folder.mkdir()
        try:
            repo = restore_database(state, folder)
            self.assertEqual(repo.get_product('ps5-digital')['first_cents'], 12345)
            self.assertEqual(export_state(repo)['observations'], state['observations'])
        finally:
            for p in folder.iterdir(): p.unlink()
            folder.rmdir()

    def cloud(self, state):
        container = Mock()
        blob = container.get_blob_client.return_value
        payload = json.dumps(state).encode()
        blob.get_blob_properties.return_value = SimpleNamespace(size=len(payload))
        blob.download_blob.return_value.readall.return_value = payload
        return AzureCatalog(container=container), blob

    def check_cloud_cache_and_same_aggregation(self):
        self.add()
        cloud, blob = self.cloud(export_state(self.repo))
        self.assertEqual(cloud.get_product('ps5-digital')['current_cents'],10001)
        cloud.list_products()
        self.assertEqual(blob.download_blob.call_count,1)

    def check_storage_failure_returns_503_not_local_fallback(self):
        cloud, blob = self.cloud({})
        client = create_app(cloud).test_client()
        self.assertEqual(client.get('/readyz').status_code,503)
        self.assertEqual(client.get('/').status_code,503)
        self.assertEqual(client.get('/healthz').status_code,200)
        self.assertNotIn('Traceback',client.get('/').text)

    def check_image_cannot_read_arbitrary_report(self):
        state=export_state(self.repo)
        state['runs']['ps5-digital']={'image_blob':'reports/secret.json'}
        cloud,blob=self.cloud(state)
        self.assertIsNone(cloud.image('ps5-digital'))
        self.assertIsNone(cloud.image('../reports'))
        self.assertEqual(cloud.container.get_blob_client.call_count,1)

    def check_security_headers(self):
        response=self.client.get('/')
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')

    def check_lease_loss_prevents_publication(self):
        lost=Mock();lost.is_set.return_value=True
        blob=Mock();container=Mock()
        with self.assertRaises(RuntimeError):publish(container,blob,Mock(),lost,export_state(self.repo))
        blob.upload_blob.assert_not_called()
        container.upload_blob.assert_not_called()

    def check_publish_passes_lease_and_keeps_snapshot(self):
        lost=Mock();lost.is_set.return_value=False
        blob=Mock();container=Mock();lease=Mock()
        publish(container,blob,lease,lost,export_state(self.repo))
        self.assertIs(blob.upload_blob.call_args.kwargs['lease'],lease)
        self.assertTrue(container.upload_blob.call_args.args[0].startswith('catalog/snapshots/'))
        lease.renew.assert_called_once()

    def check_collection_runs_requested_agent_and_publishes_evidence(self, monkeypatch):
        import webapp.cloud_job as job
        self.add()
        state=export_state(self.repo)
        state['manifest']=state['manifest'][:1]
        scratch=self.db.parent / (self.db.stem+'-worker')
        scratch.mkdir()

        @contextmanager
        def temporary(**kwargs):
            yield str(scratch)

        def launch(command, **kwargs):
            assert command[1:3] == ['-m', 'app.research_job']
            assert '--key-file' not in command
            assert 'fake-openai-canary' not in ' '.join(command)
            assert kwargs['env']['OPENAI_API_KEY']=='fake-openai-canary'
            assert kwargs['env']['MCP_API_KEY']=='fake-mcp-canary'
            assert kwargs['cwd']=='/agent'
            output=Path(command[command.index('--output-dir')+1])
            (output/'research-new.json').write_text(json.dumps(report('120', date='2026-01-03T12:00:00+00:00')))
            (output/'diagnostics-new.json').write_text(json.dumps({'budgets': {'usd_accounted':'0.001'}}))
            process=Mock()
            process.wait.return_value=0
            process.poll.return_value=0
            return process

        monkeypatch.setattr(job.tempfile,'TemporaryDirectory',temporary)
        monkeypatch.setattr(job.subprocess,'Popen',launch)
        monkeypatch.setenv('OPENAI_API_KEY','fake-openai-canary')
        monkeypatch.setenv('MCP_API_KEY','fake-mcp-canary')
        container,blob,lease,lost=Mock(),Mock(),Mock(),Mock()
        lost.is_set.return_value=False
        try:
            collect(container,blob,lease,lost,state,SimpleNamespace(max_cost_usd=.05,image_max_cost_usd=0))
            published=json.loads(blob.upload_blob.call_args.args[0])
            assert len(published['observations'])==2
            assert published['observations'][-1]['price_cents']==12000
            assert published['observations'][0]['price_cents']==10001
            assert 'fake-openai-canary' not in json.dumps(published)
            assert any(str(c.args[0]).startswith('reports/') for c in container.upload_blob.call_args_list)
        finally:
            for p in sorted(scratch.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            scratch.rmdir()



