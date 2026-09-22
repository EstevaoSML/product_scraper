"""Host-only OpenAI Responses adapter. No tools or credentials enter prompts."""
import asyncio
import base64
from decimal import Decimal
import json
from urllib.parse import quote

from app.research_contracts import DecisionEnvelope

MODEL = 'gpt-5-mini'
# Standard text rates, checked 2026-09-21. Recheck before deployment.
# https://developers.openai.com/api/docs/models/gpt-5-mini
INPUT_USD_PER_MILLION = Decimal('0.25')
OUTPUT_USD_PER_MILLION = Decimal('2.00')


class BudgetExceeded(RuntimeError):
    pass


class ProviderFailure(RuntimeError):
    pass


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
    def __init__(self, max_cost_usd, *, max_input_tokens=24000,
                 max_output_tokens=8000, max_calls=8,
                 input_rate=INPUT_USD_PER_MILLION, output_rate=OUTPUT_USD_PER_MILLION):
        self.limit = Decimal(str(max_cost_usd))
        self.input_rate, self.output_rate = Decimal(str(input_rate)), Decimal(str(output_rate))
        if (not self.limit.is_finite() or not 0 < self.limit <= 10
                or not self.input_rate.is_finite() or self.input_rate <= 0
                or not self.output_rate.is_finite() or self.output_rate <= 0
                or not 1 <= max_calls <= 8 or not 1 <= max_input_tokens <= 24000
                or not 1 <= max_output_tokens <= 8000):
            raise ValueError('Invalid model budget')
        self.max_input, self.max_output, self.max_calls = max_input_tokens, max_output_tokens, max_calls
        self.input_tokens = self.output_tokens = self.calls = 0
        self.cost = Decimal(0)

    def price(self, inputs, outputs):
        return (inputs * self.input_rate + outputs * self.output_rate) / 1_000_000

    def reserve(self, inputs, outputs):
        cost = self.price(inputs, outputs)
        if (self.calls >= self.max_calls or self.input_tokens + inputs > self.max_input
                or self.output_tokens + outputs > self.max_output or self.cost + cost > self.limit):
            raise BudgetExceeded('Model budget exhausted')
        self.calls += 1
        self.input_tokens += inputs
        self.output_tokens += outputs
        self.cost += cost
        return inputs, outputs

    def reconcile(self, reservation, usage):
        i, o = usage.get('input_tokens'), usage.get('output_tokens')
        if type(i) is not int or type(o) is not int or not 0 <= i <= reservation[0] or not 0 <= o <= reservation[1]:
            raise BudgetExceeded('Missing or inconsistent usage; reservation retained')
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
                raise BudgetExceeded('Insufficient output budget')
            body = {'model': MODEL, 'input': inputs, 'store': False,
                    'reasoning': {'effort': 'low'}, 'max_output_tokens': output_limit,
                    'text': {'format': {'type': 'json_schema', 'name': 'retail_decision',
                                       'strict': True, 'schema': DecisionEnvelope.model_json_schema()}}}
            bound = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()) + 1024
            reservation = self.budget.reserve(bound, output_limit)
            # No SDK automatic retries; all attempts consume explicit budgets.
            response = await self.http.post('https://api.openai.com/v1/responses', json=body,
                                            headers={'Authorization': 'Bearer ' + self.api_key},
                                            timeout=45)
            if response.status_code == 429 and self.retries < 2:
                try:
                    delay = float(response.headers.get('Retry-After', '1'))
                except (ValueError, TypeError):
                    # Unknown retry format: stop, never immediately resubmit.
                    raise ProviderFailure('Unsupported provider retry delay') from None
                if not 0 <= delay <= 60:
                    raise BudgetExceeded('Provider retry delay exceeds limit')
                self.retries += 1
                await self.sleep(max(1, delay))
                continue
            if response.status_code != 200:
                raise ProviderFailure('Model provider request failed')
            if len(response.content) > 131072:
                raise ProviderFailure('Oversized provider response')
            data = response.json()
            self.budget.reconcile(reservation, data.get('usage', {}))
            if data.get('status') != 'completed':
                raise ProviderFailure('Model response incomplete')
            texts = [part['text'] for item in data.get('output', []) if item.get('type') == 'message'
                     for part in item.get('content', []) if part.get('type') == 'output_text']
            if len(texts) != 1:
                raise ValueError('Missing or ambiguous model decision')
            self.guard.check(texts)
            return DecisionEnvelope.model_validate_json(texts[0]).decision.model_dump()
