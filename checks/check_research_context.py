"""Synthetic oversized pages exercise prompt fitting and evidence boundaries."""
import asyncio
import copy
import json

import pytest

from app.research_context import compact_observation, wire_size
from app.research_openai import ModelBudget, OpenAIDecider
from app.research_contracts import REQUIRED_FIELDS
from checks.check_research_openai import HTTP, response
from evals.retail_fixtures import CANARY, PRODUCT, WEBSITE, pages, product


def large_page():
    source = pages()[1]
    source['visible_text'] = 'Unrelated promotion\n' * 1000 + PRODUCT + '\nDigital edition in stock'
    source['elements'] = [dict(element_id=f'e{i+2}', action='follow_link',
        href=WEBSITE+str(i), accessible_name='Unrelated category '*8) for i in range(98)] + source['elements']
    source['elements'].append({'element_id':'e0', 'action':'search', 'placeholder':'Pesquisar'})
    return source


@pytest.mark.parametrize('limit', [1800, 4000, 9000])
def check_view_retains_observed_references_and_literal_excerpts(limit):
    source = large_page()
    original = copy.deepcopy(source)
    view_text = compact_observation(json.dumps(source), PRODUCT, limit)
    view = json.loads(view_text)
    assert wire_size(view_text) <= limit and view['agent_view_truncated'] is True
    assert view['snapshot_id'] == source['snapshot_id'] and source == original
    assert {e['element_id'] for e in view['elements']} >= {'e0','e1'}
    assert all(e in source['elements'] for e in view['elements'])
    assert all(excerpt in source['visible_text'] for excerpt in view['visible_text_excerpts'])
    assert PRODUCT in ''.join(view['visible_text_excerpts'])


def check_product_indexes_and_offer_associations_never_shift():
    source = large_page()
    source['products'] = [product(name='Wrong edition'), product(), product(name='x'*20000)]
    source['products'][1]['offers'] = [product()['offers'], product(seller='Other seller')['offers']]
    view = json.loads(compact_observation(json.dumps(source), PRODUCT, 7000))
    assert view['products'] == source['products'][:2]
    assert view['products'][1]['offers'][1]['seller']['name'] == 'Other seller'


@pytest.mark.parametrize('content', ['not json', '[]', '{}'])
def check_unrecognized_observations_are_not_silently_rewritten(content):
    assert compact_observation(content, PRODUCT, 1000) is None


def check_tiny_budget_does_not_drop_snapshot_identity():
    assert compact_observation(json.dumps(large_page()), PRODUCT, 1) is None


def check_unicode_and_escaped_text_fit_wire_budget():
    source = large_page()
    source['visible_text'] = '漢字 😀 "\\\n' * 3000 + PRODUCT
    result = compact_observation(json.dumps(source), PRODUCT, 3000)
    assert wire_size(result) <= 3000
    assert all(s in source['visible_text'] for s in json.loads(result)['visible_text_excerpts'])


def check_compacted_context_cannot_establish_not_found():
    report = dict(status='not_found', fields=dict.fromkeys(REQUIRED_FIELDS),
                  product_index=None, offer_index=None, reason='No match')
    http, budget = HTTP([response(decision={'report':report})]), ModelBudget('0.05')
    adapter = OpenAIDecider(http,CANARY,budget)
    result = asyncio.run(adapter([{'role':'system','content':'trusted'},
        {'role':'user','content':json.dumps({'product':PRODUCT})},
        {'role':'tool','content':json.dumps(large_page())}]))
    assert result['report']['status'] == 'partial'
    assert adapter.context_was_compacted and budget.calls == 1
    body = http.calls[0][1]['json']
    assert len(json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()) + 1024 <= budget.max_input
    assert body['input'][2]['role'] == 'user'
    assert 'UNTRUSTED_PAGE_DATA' in body['input'][2]['content']


def check_secrets_in_omitted_content_still_stop_dispatch():
    source = large_page()
    source['visible_text'] += CANARY
    http = HTTP([])
    with pytest.raises(ValueError):
        asyncio.run(OpenAIDecider(http,CANARY,ModelBudget('0.05'))([
            {'role':'system','content':'trusted'}, {'role':'user','content':PRODUCT},
            {'role':'tool','content':json.dumps(source)}]))
    assert not http.calls
