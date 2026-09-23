"""Host-only OpenAI Responses adapter. No tools or credentials enter prompts."""
import asyncio
import base64
from decimal import Decimal
import json
from urllib.parse import quote

import httpx

from app.research_contracts import DecisionEnvelope
from app.research_errors import BudgetExceeded, ProviderFailure
from app.research_context import VIEW_NOTE, compact_observation, wire_size

MODEL = 'gpt-5-mini'
# Standard text rates, checked 2026-09-21. Recheck before deployment.
# https://developers.openai.com/api/docs/models/gpt-5-mini
INPUT_USD_PER_MILLION = Decimal('0.25')
OUTPUT_USD_PER_MILLION = Decimal('2.00')


class SecretGuard:
    def __init__(self, values=()):
        self.forbidden = set()
        for value in values:
            if value:
                self.forbidden.update((value, quote(value, safe=''),
                                       base64.b64encode(value.encode()).decode()))

    def check(self, value):
        text = json.dumps(value, ensure_ascii=False)
        if any(secret in text for secret in self.forbidden):
            raise ValueError('Sensitive value rejected')


class ModelBudget:
    """Reserve worst-case cost before dispatch; reconcile only trusted usage.

    UTF-8 byte count plus a framing margin is a deliberately conservative input
    token bound for this text-only request. Unknown usage retains its full reserve.
    Prices are configuration, not a provider-side billing guarantee.
    """
    def __init__(self, max_cost_usd, *, max_input_tokens=32000,
                 max_output_tokens=8000, max_calls=8,
                 input_rate=INPUT_USD_PER_MILLION, output_rate=OUTPUT_USD_PER_MILLION):
        self.limit = Decimal(str(max_cost_usd))
        self.input_rate, self.output_rate = Decimal(str(input_rate)), Decimal(str(output_rate))
        if (not self.limit.is_finite() or not 0 < self.limit <= 10
                or not self.input_rate.is_finite() or self.input_rate <= 0
                or not self.output_rate.is_finite() or self.output_rate <= 0
                or not 1 <= max_calls <= 8 or not 1 <= max_input_tokens <= 32000
                or not 1 <= max_output_tokens <= 8000):
            raise ValueError('Invalid model budget')
        self.max_input, self.max_output, self.max_calls = max_input_tokens, max_output_tokens, max_calls
        self.input_tokens = self.output_tokens = self.calls = 0
        self.cost = Decimal(0)

    def price(self, inputs, outputs):
        return (inputs * self.input_rate + outputs * self.output_rate) / 1_000_000

    def reserve(self, inputs, outputs):
        cost = self.price(inputs, outputs)
        if self.calls >= self.max_calls:
            raise BudgetExceeded('model_call_budget')
        if self.input_tokens + inputs > self.max_input:
            raise BudgetExceeded('model_input_budget')
        if self.output_tokens + outputs > self.max_output:
            raise BudgetExceeded('model_output_budget')
        if self.cost + cost > self.limit:
            raise BudgetExceeded('model_cost_budget')
        self.calls += 1
        self.input_tokens += inputs
        self.output_tokens += outputs
        self.cost += cost
        return inputs, outputs

    def reconcile(self, reservation, usage):
        i, o = usage.get('input_tokens'), usage.get('output_tokens')
        if type(i) is not int or type(o) is not int or not 0 <= i <= reservation[0] or not 0 <= o <= reservation[1]:
            raise BudgetExceeded('model_usage_invalid')
        self.input_tokens -= reservation[0] - i
        self.output_tokens -= reservation[1] - o
        self.cost -= self.price(reservation[0] - i, reservation[1] - o)

    def summary(self):
        return {'model': MODEL, 'calls': self.calls, 'input_tokens_accounted': self.input_tokens,
                'output_tokens_accounted': self.output_tokens, 'usd_accounted': str(self.cost)}


class OpenAIDecider:
    def __init__(self, http, api_key, budget, *, guard=None, sleep=asyncio.sleep):
        self.http, self.api_key, self.budget = http, api_key, budget
        self.guard = guard or SecretGuard([api_key])
        self.sleep = sleep
        self.retries = 0
        self.context_was_compacted = False

    async def __call__(self, messages):
        # Preserve system/task boundary; tool observations are JSON DATA in user
        # content, not forged function-call records or elevated instructions.
        current = [m for m in messages if m['role'] == 'tool'][-1:]
        corrections = [m for m in messages[2:] if m['role'] == 'system'][-1:]
        previous = [m for m in messages if m['role'] == 'assistant'][-1:]
        selected = messages[:2] + previous + current + corrections
        self.guard.check(selected)
        inputs = [{'role': 'developer' if m['role'] == 'system' else
                   ('user' if m['role'] == 'tool' else m['role']),
                   'content': ('UNTRUSTED_PAGE_DATA\n' if m['role'] == 'tool' else '') + m['content']}
                  for m in selected]
        while True:
            output_limit = min(2048, self.budget.max_output - self.budget.output_tokens)
            if output_limit < 256:
                raise BudgetExceeded('model_output_budget')
            body = {'model': MODEL, 'input': inputs, 'store': False,
                    'reasoning': {'effort': 'low'}, 'max_output_tokens': output_limit,
                    'text': {'format': {'type': 'json_schema', 'name': 'retail_decision',
                                       'strict': True, 'schema': DecisionEnvelope.model_json_schema()}}}
            bound = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()) + 1024
            remaining = self.budget.max_input - self.budget.input_tokens
            if current:
                # Fit only the current observation; trusted instructions/schema
                # and the user's exact task are never truncated.
                fitted = [dict(item) for item in inputs]
                index = len(messages[:2]) + len(previous)
                fitted[index]['content'] = 'UNTRUSTED_PAGE_DATA\n'
                fitted.append({'role': 'developer', 'content': VIEW_NOTE})
                body['input'] = fitted
                overhead = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()) + 1024
                try:
                    task = json.loads(messages[1]['content'])
                    query = task.get('product', '') if isinstance(task, dict) else ''
                except (ValueError, IndexError):
                    query = ''
                stage = current[0].get('research_stage', 'homepage')
                # Keep 8k conservative input units for later extraction.
                allowance = remaining - (8000 if stage != 'product' else 0)
                view_limit = allowance - overhead + wire_size('')
                if stage != 'product':
                    view_limit = min(view_limit, 4000 if stage == 'homepage' else 7000)
                view = compact_observation(current[0]['content'], query if isinstance(query, str) else '',
                                           view_limit, stage=stage)
                if view is not None:
                    fitted[index]['content'] += view
                    self.context_was_compacted = True
                else:
                    # Non-observation tool error text stays bounded by reserve.
                    body['input'] = inputs
                bound = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()) + 1024
            reservation = self.budget.reserve(bound, output_limit)
            # No SDK automatic retries; all attempts consume explicit budgets.
            try:
                response = await self.http.post('https://api.openai.com/v1/responses', json=body,
                                                headers={'Authorization': 'Bearer ' + self.api_key},
                                                timeout=45)
            except httpx.TimeoutException:
                raise ProviderFailure('provider_timeout') from None
            except httpx.RequestError:
                raise ProviderFailure('provider_connection') from None
            # Read only allowlisted billing codes. Never retain message/param/body.
            if response.status_code == 429:
                try:
                    error = response.json().get('error', {})
                except (ValueError, AttributeError):
                    error = {}
                quota_codes = ('insufficient_quota', 'billing_hard_limit_reached',
                               'organization_spend_limit_exceeded', 'project_spend_limit_exceeded',
                               'organization_usage_limit_exceeded')
                if isinstance(error, dict) and (error.get('code') in quota_codes or error.get('type') == 'insufficient_quota'):
                    raise ProviderFailure('provider_quota', http_status=429)
            if response.status_code == 429 and self.retries < 2:
                try:
                    delay = float(response.headers.get('Retry-After', '1'))
                except (ValueError, TypeError):
                    # Unknown retry format: stop, never immediately resubmit.
                    raise ProviderFailure('provider_retry_delay', http_status=429) from None
                if not 0 <= delay <= 60:
                    raise ProviderFailure('provider_retry_delay', http_status=429)
                self.retries += 1
                await self.sleep(max(1, delay))
                continue
            if response.status_code != 200:
                code = {400: 'provider_bad_request', 401: 'provider_authentication',
                        403: 'provider_permission', 404: 'provider_not_found',
                        429: 'provider_rate_limit'}.get(response.status_code,
                        'provider_unavailable' if response.status_code >= 500 else 'provider_http')
                raise ProviderFailure(code, http_status=response.status_code)
            if len(response.content) > 131072:
                raise ProviderFailure('provider_response_size')
            data = response.json()
            self.budget.reconcile(reservation, data.get('usage', {}))
            if data.get('status') != 'completed':
                details = data.get('incomplete_details') or {}
                code = 'provider_output_limit' if isinstance(details, dict) and details.get('reason') == 'max_output_tokens' else 'provider_incomplete'
                raise ProviderFailure(code)
            texts = [part['text'] for item in data.get('output', []) if item.get('type') == 'message'
                     for part in item.get('content', []) if part.get('type') == 'output_text']
            if len(texts) != 1:
                raise ValueError('Missing or ambiguous model decision')
            self.guard.check(texts)
            envelope = DecisionEnvelope.model_validate_json(texts[0])
            decision = envelope.decision.model_dump()
            report = decision.get('report')
            if self.context_was_compacted and report and report['status'] == 'not_found':
                report['status'] = 'partial'
                report['reason'] = 'Observation content was omitted to fit the input budget; absence cannot be established.'
            result = envelope.model_dump()
            result['decision'] = decision
            return result
