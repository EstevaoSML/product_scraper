"""Monthly expansion, reusable images and safe September triggering; no paid calls."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock
import pytest
from webapp.portfolio import worker, images
from webapp.deploy.portfolio.operations import start_template, OperatorError


def check_fifty_products_preserve_existing_ids_and_state():
    original = json.loads((worker.Path(worker.__file__).parent / 'products.json').read_text(encoding='utf-8'))
    assert [p['id'] for p in original[:10]] == ['ps5-digital', 'logitech-g502', 'hyperx-alloy', 'kingston-nv3', 'hyperx-cloud', 'logitech-mx-master', 'samsung-990-pro', 'jbl-flip-6', 'xbox-series-s', 'lg-24mp400']
    assert len(original) == 50 and len({p['id'] for p in original}) == 50
    assert all(set(p) == {'id', 'name', 'query', 'category'} for p in original)
    state = dict(schema_version=1, manifest=original[:10], observations=[{'evidence':'preserve'}], runs={'ps5-digital':{'image_blob':'keep'}})
    copy = deepcopy(state)
    upgraded = worker.refresh_manifest(state)
    assert upgraded['manifest'][:10] == state['manifest']
    assert upgraded['observations'] == state['observations']
    assert upgraded['runs'] == state['runs']
    assert state == copy
    assert worker.ACTIVE_RETAILERS == ('kabum', 'amazon', 'americanas')


def check_september_legacy_reservations_do_not_block_new_retailers():
    ledger = dict(schema_version=1, month='2026-09', attempts={
        f'p{i}/casasbahia':dict(reserved_cents=5, status='failed') for i in range(10)})
    for i in range(50):
        for retailer in worker.ACTIVE_RETAILERS:
            assert worker.reserve(ledger, '2026-09', f'p{i}', retailer)
    assert len(ledger['attempts']) == 160
    assert sum(a['reserved_cents'] for a in ledger['attempts'].values()) == 800
    assert worker.reserve(ledger, '2026-09', 'extra', 'kabum') is None
    with pytest.raises(ValueError):
        worker.reserve(ledger, '2026-09', 'extra', 'casasbahia')


def check_images_cap_and_no_refund_or_retry():
    ledger = {}
    for i in range(50):
        assert images.reserve_image(ledger, str(i))
        ledger['images'][str(i)]['status'] = 'failed'
        assert not images.reserve_image(ledger, str(i))
    assert not images.reserve_image(ledger, 'extra')
    assert sum(a['reserved_cents'] for a in ledger['images'].values()) == 250


@pytest.mark.parametrize('bad', [[], {'p':None}, {'p':{'reserved_cents':0}}])
def check_corrupt_image_ledger_fails_closed(bad):
    with pytest.raises(ValueError):
        images.reserve_image({'images':bad}, 'p')


def check_existing_image_never_calls_api_or_reserves(monkeypatch, tmp_path):
    generate = Mock()
    monkeypatch.setattr(images, 'generate', generate)
    state = dict(runs={'p': {'image_blob':'images/p/existing.png'}})
    images.ensure_image(Mock(), Mock(), Mock(), Mock(), state, {'id':'p'}, {}, Mock(), tmp_path, 'fixture')
    generate.assert_not_called()


@pytest.mark.parametrize('lost_after', [False, True])
def check_image_saved_once_and_reserved_before_paid_request(monkeypatch, tmp_path, lost_after):
    import webapp.cloud_job
    ledger, persisted, calls = {}, [], []
    lost = Mock()
    lost.is_set.return_value = False
    state = dict(schema_version=1, manifest=[], observations=[], runs={})
    def save():
        persisted.append(deepcopy(ledger))
    async def generate(seed, key):
        assert persisted[-1]['images']['p']['status'] == 'reserved'
        calls.append(seed['id'])
        if lost_after:
            lost.is_set.return_value = True
        return dict(status='generated', usd_accounted='0.03', budget_exceeded=False), b'\x89PNG\r\n\x1a\nfixture'
    monkeypatch.setattr(images, 'generate', generate)
    publish = Mock()
    monkeypatch.setattr(webapp.cloud_job, 'publish', publish)
    args = (Mock(), Mock(), Mock(), lost, state, {'id':'p', 'query':'Fixture'}, ledger, save, tmp_path, 'canary')
    if lost_after:
        with pytest.raises(RuntimeError):
            images.ensure_image(*args)
        publish.assert_not_called()
        assert ledger['images']['p']['status'] == 'reserved'
    else:
        images.ensure_image(*args)
        images.ensure_image(*args)
        assert state['runs']['p']['image_status'] == 'generated'
        assert ledger['images']['p']['status'] == 'generated'
        publish.assert_called_once()
    assert calls == ['p']


def check_month_cannot_be_backdated_and_override_is_frozen():
    current = datetime.now(timezone.utc).strftime('%Y-%m')
    assert worker.collection_month(current) == current
    with pytest.raises(ValueError):
        worker.collection_month('2000-09')
    job = {'template': {'containers':[{'name':'portfolio','env':[{'name':'COLLECTION_MONTH','value':'2000-09'}]}]}}
    with pytest.raises(OperatorError):
        start_template(deepcopy(job), 'collect', 150, '2000-09')
    result = start_template(deepcopy(job), 'collect', 150, current)
    env = {e['name']:e['value'] for e in result['containers'][0]['env']}
    assert env['MAX_TASKS'] == '150' and env['COLLECTION_MONTH'] == current
    assert all(e['name'] != 'COLLECTION_MONTH' for e in start_template(job, 'smoke', 1)['containers'][0]['env'])


def check_powershell_rejects_past_month_before_cloud_or_terraform():
    from pathlib import Path
    from checks.check_web_deploy import powershell
    script = str(Path(__file__).resolve().parents[1] / 'webapp/deploy/portfolio/deploy.ps1').replace("'", "''")
    result = powershell("function az { throw 'Azure must not run' }; function terraform { throw 'Terraform must not run' }; & '" + script + "' -Action CollectMonthly -Month '2000-09'")
    assert result.returncode != 0
    assert 'current UTC month' in result.stderr

