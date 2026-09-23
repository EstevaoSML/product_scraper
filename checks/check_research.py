"""Deterministic safety and grounding regressions; no network or model keys."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from app.research import research, validate_report
from app.research_contracts import DecisionEnvelope, REQUIRED_FIELDS
from evals.retail_fixtures import (CANARY, PRODUCT, WEBSITE, SyntheticMCP, cases,
                                    decisions, envelope, pages, report, scripted)


@pytest.mark.parametrize('case', cases(), ids=lambda c: c['id'])
def check_corpus_through_real_entry_point(case):
    client, metrics = SyntheticMCP(case['pages']), {}
    result = asyncio.run(research(client, scripted(decisions(case['report'])), WEBSITE, PRODUCT, metrics=metrics))
    assert result['status'] == case['report']['status']
    for key, fact in case['report']['fields'].items():
        assert result['product'][key] == (fact['value'] if fact else None)
    assert client.calls[-1][0] == 'close_session'
    assert metrics['cleanup'] == 'closed'
    assert CANARY not in json.dumps(result)


@pytest.mark.parametrize('mutation', ['value', 'quote', 'snapshot', 'missing', 'extra', 'cross_snapshot', 'seller', 'variant', 'aggregate'])
def check_report_rejects_unsupported_or_mixed_facts(mutation):
    observation, candidate = pages()[2], report()
    snapshots = {observation['snapshot_id']: observation}
    if mutation == 'value': candidate['fields']['price']['value'] = '1.00'
    if mutation == 'quote': candidate['fields']['name']['quote'] = 'fabricated'
    if mutation == 'snapshot': candidate['fields']['price']['snapshot_id'] = 'f' * 32
    if mutation == 'missing': candidate['fields']['name'] = None
    if mutation == 'extra': candidate['fields']['token'] = None
    if mutation == 'cross_snapshot':
        snapshots['f' * 32] = {**observation, 'url': WEBSITE + 'other'}
        candidate['fields']['price']['snapshot_id'] = 'f' * 32
        candidate['status'] = 'partial'
    if mutation == 'seller':
        observation['visible_text'] += ' Loja Verde'
        candidate['fields']['seller'].update(value='Loja Verde', quote='Loja Verde')
    if mutation == 'variant': candidate['fields']['variant'].update(value='Disc', quote='Disc')
    if mutation == 'aggregate': observation['products'][0]['offers']['@type'] = 'AggregateOffer'
    with pytest.raises(ValueError):
        validate_report(candidate, snapshots, PRODUCT)


def check_visible_pix_and_installment_prices_do_not_invalidate_direct_offer():
    observation = pages()[2]
    observation['visible_text'] += '\nR$ 2.199,90 no Pix\n10x de R$ 249,99\nDe R$ 2.899,90'
    candidate = report(observation)
    result = validate_report(candidate, {observation['snapshot_id']: observation}, PRODUCT)
    assert result['status'] == 'complete'
    assert result['product']['price'] == observation['products'][0]['offers']['price']


def check_redundant_product_inspection_is_not_dispatched_or_recorded_twice():
    client, metrics, assessments = SyntheticMCP(), {}, []
    sequence = decisions()[:2] + [
        envelope({'tool': 'inspect_page', 'arguments': {}}, positive=True),
        envelope({'report': report()}, positive=True),
    ]
    result = asyncio.run(research(client, scripted(sequence), WEBSITE, PRODUCT,
                                  metrics=metrics, on_assessment=assessments.append))
    assert result['status'] == 'complete'
    assert [name for name, _ in client.calls] == [
        'open_page', 'search_site', 'follow_link', 'close_session']
    assert metrics['skipped_inspections'] == 1
    assert len({entry['url'] for entry in assessments}) == len(assessments) == 3


@pytest.mark.parametrize('decision', [
    {'tool': 'execute_script', 'arguments': {}},
    {'tool': 'scrape_html', 'arguments': {'url': 'https://attacker.invalid/'}},
    {'tool': 'inspect_page', 'arguments': {'session_id': 'foreign'}},
    {'tool': 'search_site', 'arguments': {'snapshot_id': 'f'*32, 'element_id': 'e0', 'query': PRODUCT}},
    {'tool': 'search_site', 'arguments': {'snapshot_id': f'{1:032x}', 'element_id': 'e9', 'query': PRODUCT}},
    {'tool': 'search_site', 'arguments': {'snapshot_id': f'{1:032x}', 'element_id': 'e0', 'query': CANARY}},
    {'tool': 'follow_link', 'arguments': {'snapshot_id': f'{1:032x}', 'element_id': 'e0'}},
    {'report': report()},
])
def check_untrusted_decisions_never_dispatch(decision):
    client = SyntheticMCP()
    async def decide(messages): return decision
    result = asyncio.run(research(client, decide, WEBSITE, PRODUCT))
    assert result['status'] == 'partial'
    assert [n for n, _ in client.calls] == ['open_page', 'close_session']


@pytest.mark.parametrize('url', ['http://shop.example/', 'https://127.0.0.1/',
                                'https://169.254.169.254/', 'https://shop.example/checkout'])
def check_invalid_destination_before_open(url):
    client = SyntheticMCP()
    with pytest.raises(ValueError): asyncio.run(research(client, None, url, PRODUCT))
    assert client.calls == []


@pytest.mark.parametrize('href', ['https://attacker.invalid/', 'https://shop.example/cart', 'http://169.254.169.254/'])
def check_observed_link_still_requires_authorization(href):
    observations = pages()
    observations[1]['elements'][0]['href'] = href
    client = SyntheticMCP(observations)
    result = asyncio.run(research(client, scripted(decisions()), WEBSITE, PRODUCT))
    assert result['status'] == 'partial'
    assert 'follow_link' not in [n for n, _ in client.calls]


def check_blocked_after_last_operation_never_calls_model_again():
    observations = pages()
    observations[1]['status'] = 'blocked'
    client = SyntheticMCP(observations)
    result = asyncio.run(research(client, scripted(decisions()), WEBSITE, PRODUCT, max_decisions=1))
    assert result['status'] == 'blocked'
    assert [n for n, _ in client.calls] == ['open_page', 'search_site', 'close_session']


@pytest.mark.parametrize('limit', ['operations', 'links', 'decisions'])
def check_budgets(limit):
    client, metrics = SyntheticMCP(), {}
    options = {'operations': {'max_operations': 1}, 'links': {'max_links': 0}, 'decisions': {'max_decisions': 1}}[limit]
    result = asyncio.run(research(client, scripted(decisions()), WEBSITE, PRODUCT, metrics=metrics, **options))
    assert result['status'] == 'partial'
    assert client.calls[-1][0] == 'close_session'
    assert len(client.calls) <= 3


def check_cancellation_waits_for_cleanup():
    async def scenario():
        client = SyntheticMCP()
        entered = asyncio.Event()
        async def decide(messages):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(research(client, decide, WEBSITE, PRODUCT))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert client.calls[-1][0] == 'close_session'
    asyncio.run(scenario())


def check_time_budget_and_transport_exception_cleanup():
    for exception in (TimeoutError(), RuntimeError(CANARY)):
        client = SyntheticMCP()
        async def decide(messages): raise exception
        result = asyncio.run(research(client, decide, WEBSITE, PRODUCT))
        assert result['status'] == 'partial' and CANARY not in json.dumps(result)
        assert client.calls[-1][0] == 'close_session'


def check_malformed_snapshot_still_closes_known_session():
    observations = pages()
    observations[0].pop('snapshot_id')
    client = SyntheticMCP(observations)
    assert asyncio.run(research(client, None, WEBSITE, PRODUCT))['status'] == 'partial'
    assert client.calls[-1][0] == 'close_session'


def check_model_never_sees_session_capability():
    client = SyntheticMCP()
    async def decide(messages):
        assert 's' * 43 not in json.dumps(messages)
        raise TimeoutError()
    asyncio.run(research(client, decide, WEBSITE, PRODUCT))


def check_busy_retries_respect_delay_and_global_budget():
    class Busy(SyntheticMCP):
        async def call_tool(self, name, args):
            self.calls.append((name, args))
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=json.dumps(
                {'http_status': 429, 'retry_after_seconds': 2}))])
    client, waits, stats = Busy(), [], {}
    async def sleep(delay): waits.append(delay)
    result = asyncio.run(research(client, None, WEBSITE, PRODUCT, sleep=sleep, metrics=stats))
    assert result['status'] == 'partial'
    assert waits == [2, 2] and len(client.calls) == 3
    assert stats['retries'] == 2


def check_snapshot_invalidated_after_search():
    sequence = decisions()
    sequence[1]['decision']['arguments']['snapshot_id'] = f'{1:032x}'
    client = SyntheticMCP()
    asyncio.run(research(client, scripted(sequence), WEBSITE, PRODUCT))
    assert 'follow_link' not in [n for n, _ in client.calls]


def check_search_refinements_and_pagination_are_hidden_and_rejected():
    observations = pages()
    observations[1]['elements'].insert(0, {'element_id':'e9', 'action':'follow_link',
        'href':WEBSITE+'search?page=2', 'accessible_name':'More results'})
    choices = iter(decisions())
    async def decide(messages):
        decision = next(choices)
        if decision['decision'].get('tool') == 'follow_link':
            observation = json.loads(messages[-1]['content'])
            assert 'e9' not in {element['element_id'] for element in observation['elements']}
        return decision
    client = SyntheticMCP(observations)
    assert asyncio.run(research(client, decide, WEBSITE, PRODUCT))['status'] == 'complete'
    bad = decisions()
    bad[1]['decision']['arguments']['element_id'] = 'e9'
    client = SyntheticMCP(observations)
    assert asyncio.run(research(client, scripted(bad), WEBSITE, PRODUCT))['status'] == 'partial'
    assert 'follow_link' not in [name for name, _ in client.calls]


def check_agent_ps5_alias_assessment_is_saved():
    requested = 'PS5 Digital Edition'
    product_name = 'Console PlayStation 5 Edição Digital'
    observations = pages()
    observations[2]['visible_text'] = product_name
    observations[2]['products'][0]['name'] = product_name
    sequence = decisions()
    sequence[0]['decision']['arguments']['query'] = requested
    sequence[2]['assessment'].update(product_name=product_name,
                                     description='Console digital PlayStation 5.')
    sequence[2]['decision'] = {'report': {'status': 'partial',
        'fields': dict.fromkeys(REQUIRED_FIELDS), 'product_index': None,
        'offer_index': None, 'reason': 'Offer details unavailable'}}
    client, assessments = SyntheticMCP(observations), []
    asyncio.run(research(client, scripted(sequence), WEBSITE, requested,
                         on_assessment=assessments.append))
    assert assessments[-1]['has_ps5_info'] is True
    assert assessments[-1]['ps5_info']['product_name'] == product_name
    assert client.calls[-1][0] == 'close_session'


def check_invalid_limits():
    with pytest.raises(ValueError): asyncio.run(research(None, None, WEBSITE, PRODUCT, max_operations=11))


@pytest.mark.parametrize('quote', [PRODUCT, PRODUCT.upper(), PRODUCT + '!!!'])
def check_echoed_query_is_not_product_evidence(quote):
    observation = pages()[2]
    observation.update(products=[], visible_text=quote,
                       url=WEBSITE+'s?q=Console+Nova+Digital+1TB&page=1')
    candidate = report(observation, product_index=None, offer_index=None, status='partial')
    for field in ('name', 'variant'):
        candidate['fields'][field]['quote'] = quote
    result = validate_report(candidate, {observation['snapshot_id']:observation}, PRODUCT)
    assert result['status'] == 'partial' and not any(result['product'].values())
    assert result['evidence'] == {} and 'search query' in result['reason']


def check_pagination_query_echo_returns_empty_partial_and_closes():
    observations = pages()
    observations[2].update(products=[], visible_text=PRODUCT, url=WEBSITE+'s?page=1')
    candidate = report(observations[2], product_index=None, offer_index=None, status='partial')
    client, stats = SyntheticMCP(observations), {}
    result = asyncio.run(research(client, scripted(decisions(candidate)), WEBSITE, PRODUCT, metrics=stats))
    assert result['status'] == 'partial' and result['evidence'] == {}
    assert all(value is None for value in result['product'].values())
    assert stats['invalid'] == 0 and stats['cleanup'] == 'closed'


def check_structured_product_with_exact_query_title_still_valid():
    observation = pages()[2]
    candidate = report(observation)
    for field in ('name', 'variant'):
        candidate['fields'][field]['quote'] = PRODUCT
    result = validate_report(candidate, {observation['snapshot_id']:observation}, PRODUCT)
    assert result['status'] == 'complete'


def check_ps5_digital_edition_matches_portuguese_product_identity():
    observation = pages()[2]
    name = 'Console PS5 Sony Digital 825GB Bundle | Americanas'
    observation['products'][0]['name'] = name
    observation['visible_text'] = ('Console PlayStation 5 Edição Digital Sony Bundle 825GB - Americanas\n'
                                   + observation['visible_text'])
    candidate = report(observation)
    evidence = json.dumps(observation['products'], ensure_ascii=False)
    candidate['fields']['name'].update(value=name, quote=evidence)
    candidate['fields']['variant'].update(value='Digital 825GB Bundle', quote=evidence)
    result = validate_report(candidate, {observation['snapshot_id']: observation},
                             'PS5 Digital Edition')
    assert result['status'] == 'complete'


def check_identity_rejection_has_safe_actionable_feedback_and_cleanup():
    candidate = report()
    client, stats = SyntheticMCP(), {}
    # The requested Pro variant is not present in the synthetic Product name.
    sequence = decisions()[:2] + [envelope({'report': candidate})] * 3
    sequence[0]['decision']['arguments']['query'] = PRODUCT + ' Pro'
    messages_seen = []
    iterator = iter(sequence)
    async def decide(messages):
        messages_seen.append(messages)
        return next(iterator)
    result = asyncio.run(research(client, decide, WEBSITE, PRODUCT + ' Pro', metrics=stats))
    assert result['status'] == 'partial' and 'requested_identity_mismatch' in result['reason']
    assert [r['code'] for r in stats['rejections']] == ['requested_identity_mismatch'] * 3
    assert 'distinguishing requested term' in messages_seen[-1][-1]['content']
    assert stats['invalid'] == 3 and stats['cleanup'] == 'closed'


def check_rejection_feedback_never_echoes_exception_secrets():
    from app.research_errors import decision_feedback
    for error in [ValueError(CANARY), KeyError(CANARY), TypeError(CANARY)]:
        code, feedback = decision_feedback(error)
        assert code == 'invalid_decision_schema'
        assert CANARY not in feedback


def check_grounding_feedback_allows_a_corrected_report():
    bad = report()
    bad['fields']['price']['value'] = 'invented'
    client, stats = SyntheticMCP(), {}
    result = asyncio.run(research(client, scripted(decisions(bad) + [envelope({'report': report()}, positive=True)]),
                                  WEBSITE, PRODUCT, metrics=stats))
    assert result['status'] == 'complete'
    assert stats['rejections'][0]['code'] == 'ungrounded_evidence'
    assert stats['cleanup'] == 'closed'


@pytest.mark.parametrize('known', [True, False])
def check_failure_diagnostics_preserve_cleanup_and_hide_secrets(known):
    from app.research_errors import BudgetExceeded
    from evals.retail_fixtures import CANARY
    client, stats = SyntheticMCP(), {}
    async def decide(messages):
        raise BudgetExceeded('model_input_budget') if known else RuntimeError(CANARY)
    result = asyncio.run(research(client, decide, WEBSITE, PRODUCT, metrics=stats))
    code = 'model_input_budget' if known else 'unexpected_failure'
    assert stats['failure'] == {'code':code, 'stage':'model_decision'}
    assert code in result['reason'] and result['status'] == 'partial'
    assert CANARY not in json.dumps([result, stats])
    assert stats['cleanup'] == 'closed' and client.calls[-1][0] == 'close_session'


def check_not_found_contract():
    candidate = {'status': 'not_found', 'fields': dict.fromkeys(REQUIRED_FIELDS),
                 'product_index': None, 'offer_index': None, 'reason': 'No matching edition'}
    result = validate_report(candidate, {})
    assert result['status'] == 'not_found' and not any(result['product'].values())


def check_output_schema_has_closed_objects():
    schema = DecisionEnvelope.model_json_schema()
    assert schema['additionalProperties'] is False
    assert all(v.get('additionalProperties') is False for v in schema['$defs'].values())
    assert 'image' not in json.dumps(schema).casefold()


def check_elapsed_deadline_prevents_dispatch_after_model_returns():
    now = [0]
    client = SyntheticMCP()
    async def decide(messages):
        now[0] = 171
        return decisions()[0]
    result = asyncio.run(research(client, decide, WEBSITE, PRODUCT, clock=lambda: now[0]))
    assert result['status'] == 'partial'
    assert [n for n, _ in client.calls] == ['open_page', 'close_session']


@pytest.mark.parametrize('mode', ['error', 'exception', 'not_closed'])
def check_cleanup_failure_is_recorded(mode):
    class BrokenClose(SyntheticMCP):
        async def call_tool(self, name, args):
            if name == 'close_session':
                self.calls.append((name, args))
                if mode == 'exception': raise TimeoutError()
                return SimpleNamespace(is_error=mode == 'error', structured_content={'closed': False})
            return await super().call_tool(name, args)
    client, stats = BrokenClose(), {}
    asyncio.run(research(client, scripted(decisions()), WEBSITE, PRODUCT, metrics=stats))
    assert stats['cleanup'] == 'failed'
    assert client.calls[-1][0] == 'close_session'


def check_refresh_after_stale_reference_then_resume():
    class Stale(SyntheticMCP):
        def __init__(self):
            super().__init__()
            self.rejected = False
        async def call_tool(self, name, args):
            if name == 'follow_link' and not self.rejected:
                self.rejected = True
                self.calls.append((name, args))
                return SimpleNamespace(is_error=True, content=[SimpleNamespace(text='{"code":"stale_snapshot"}')])
            return await super().call_tool(name, args)
    client = Stale()
    result = asyncio.run(research(client, scripted(decisions()[:2] + [
        envelope({'tool': 'inspect_page', 'arguments': {}}),
        envelope({'report': {'status':'partial','fields':dict.fromkeys(REQUIRED_FIELDS),
                    'product_index':None,'offer_index':None,'reason':'Unavailable'}}, positive=True)]), WEBSITE, PRODUCT))
    assert result['status'] == 'partial'
    assert [n for n, _ in client.calls] == ['open_page','search_site','follow_link','inspect_page','close_session']
