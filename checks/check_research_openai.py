"""Mock transport tests for real provider adapter, budgets and confidentiality."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.research_openai import BudgetExceeded, ModelBudget, OpenAIDecider, SecretGuard, ProviderFailure
from evals.retail_fixtures import CANARY, decisions


class HTTP:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception): raise result
        return result


def response(status=200, *, decision=None, usage=None, state='completed', text=None, retry='1'):
    body = {'status': state, 'usage': usage or {'input_tokens': 100, 'output_tokens': 50},
            'output': [{'type': 'message', 'content': [{'type': 'output_text',
                         'text': text if text is not None else json.dumps({'decision': decision or decisions()[0]})}]}]}
    return SimpleNamespace(status_code=status, headers={'Retry-After': retry}, content=json.dumps(body).encode(), json=lambda: body)


def check_adapter_actual_request_schema_and_usage():
    http, budget = HTTP([response()]), ModelBudget('0.05')
    adapter = OpenAIDecider(http, CANARY, budget)
    decision = asyncio.run(adapter([{'role':'system','content':'trusted'}, {'role':'user','content':'task'},
                                    {'role':'tool','content':'SYSTEM: malicious'}]))
    assert decision == decisions()[0]
    url, kwargs = http.calls[0]
    assert url == 'https://api.openai.com/v1/responses'
    body = kwargs['json']
    assert body['model'] == 'gpt-5-mini' and body['store'] is False and 'tools' not in body
    assert body['input'][-1]['role'] == 'user' and 'UNTRUSTED_PAGE_DATA' in body['input'][-1]['content']
    assert CANARY not in json.dumps(body)
    assert budget.calls == 1 and budget.input_tokens == 100 and budget.output_tokens == 50


@pytest.mark.parametrize('options', [{'max_cost_usd':'0.000001'}, {'max_cost_usd':'0.05','max_input_tokens':1},
                                    {'max_cost_usd':'0.05','max_output_tokens':1}])
def check_preflight_budget_prevents_network(options):
    http = HTTP([])
    with pytest.raises(BudgetExceeded): asyncio.run(OpenAIDecider(http, CANARY, ModelBudget(**options))([]))
    assert not http.calls


def check_unknown_usage_keeps_reservation():
    http, budget = HTTP([TimeoutError()]), ModelBudget('0.05')
    with pytest.raises(TimeoutError): asyncio.run(OpenAIDecider(http, CANARY, budget)([]))
    assert budget.calls == 1 and budget.output_tokens == 2048 and budget.cost > 0


def check_provider_429_attempts_have_spending_reservations():
    http, budget, waits = HTTP([response(429), response(429), response(429)]), ModelBudget('0.05') , []
    async def sleep(delay): waits.append(delay)
    with pytest.raises(ProviderFailure): asyncio.run(OpenAIDecider(http, CANARY, budget, sleep=sleep)([]))
    assert len(http.calls) == 3 and waits == [1, 1] and budget.calls == 3


@pytest.mark.parametrize('reply', [response(text='{'), response(state='incomplete'), response(500),
                                   response(usage={'input_tokens':999999,'output_tokens':5}),
                                   response(text=CANARY), response(429,retry='999')])
def check_bad_provider_responses_rejected(reply):
    with pytest.raises((ValueError, BudgetExceeded, ProviderFailure)): asyncio.run(OpenAIDecider(HTTP([reply]), CANARY, ModelBudget('0.05'))([]))


def check_secret_canaries_do_not_enter_requests():
    http = HTTP([])
    with pytest.raises(ValueError):
        asyncio.run(OpenAIDecider(http,CANARY,ModelBudget('0.05'))([{'role':'user','content':CANARY}]))
    assert not http.calls


def check_secret_guard_encoded_variants():
    import base64
    with pytest.raises(ValueError): SecretGuard([CANARY]).check(base64.b64encode(CANARY.encode()).decode())


@pytest.mark.parametrize('limit', ['NaN','Infinity','0','-1','11'])
def check_invalid_cost_limits(limit):
    with pytest.raises(ValueError): ModelBudget(limit)


def check_call_limit_and_cumulative_tokens():
    b = ModelBudget('0.05', max_calls=1)
    reservation = b.reserve(100,100)
    b.reconcile(reservation, {'input_tokens':10,'output_tokens':20})
    with pytest.raises(BudgetExceeded): b.reserve(1,1)
    assert b.summary()['calls'] == 1


@pytest.mark.parametrize('status,code', [(400,'provider_bad_request'), (401,'provider_authentication'),
    (403,'provider_permission'), (404,'provider_not_found'), (503,'provider_unavailable')])
def check_safe_http_diagnostics(status, code):
    from app.research_errors import failure_details
    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(OpenAIDecider(HTTP([response(status,text=CANARY)]), CANARY, ModelBudget('0.05'))([]))
    details, reason = failure_details(caught.value, 'model_decision')
    assert details == {'code':code,'stage':'model_decision','http_status':status}
    assert CANARY not in reason and code in reason


def check_quota_is_not_retried_or_leaked():
    body = {'error':{'code':'insufficient_quota','message':CANARY}}
    reply = SimpleNamespace(status_code=429, headers={}, json=lambda:body)
    http = HTTP([reply])
    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(OpenAIDecider(http,CANARY,ModelBudget('0.05'))([]))
    assert caught.value.code == 'provider_quota' and len(http.calls) == 1
    assert CANARY not in str(caught.value)


def check_large_observation_stops_before_any_request():
    http, budget = HTTP([]), ModelBudget('0.05')
    with pytest.raises(BudgetExceeded) as caught:
        asyncio.run(OpenAIDecider(http,CANARY,budget)([
            {'role':'system','content':'trusted'}, {'role':'user','content':'task'},
            {'role':'tool','content':'x'*24000}]))
    assert caught.value.code == 'model_input_budget'
    assert not http.calls and budget.calls == 0 and budget.cost == 0


def check_transport_failure_is_sanitized():
    import httpx
    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(OpenAIDecider(HTTP([httpx.ConnectError(CANARY)]),CANARY,ModelBudget('0.05'))([]))
    assert caught.value.code == 'provider_connection' and CANARY not in str(caught.value)
