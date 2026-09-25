"""Image generation contracts, credentials and budgets: synthetic HTTP only."""
import asyncio
import base64
import json

import pytest

from app.research_images import MODEL, ProductImageRequest, ProductImageTool, image_budget
from app.research_openai import SecretGuard
from evals.retail_fixtures import CANARY, PRODUCT

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6VwAAAABJRU5ErkJggg==')


class Stream:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def aiter_bytes(self): yield self.body


class HTTP:
    def __init__(self, body=None, status=200):
        self.calls = []
        self.reply = Stream(body if body is not None else json.dumps({
            'data':[{'b64_json':base64.b64encode(PNG).decode()}],
            'usage':{'input_tokens':100,'output_tokens':196}}).encode(), status)
    def stream(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        return self.reply


def tool(http, budget='0.05'):
    return ProductImageTool(http,CANARY,budget,SecretGuard([CANARY,'MCP_SECRET_CANARY']))


def check_image_request_uses_exact_model_and_single_attempt():
    http = HTTP()
    generator = tool(http)
    result, data = asyncio.run(generator.generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert result.status == 'generated' and data == PNG
    assert result.synthetic and result.accounting == 'provider_usage'
    method, url, args = http.calls[0]
    assert method == 'POST' and url == 'https://api.openai.com/v1/images/generations'
    assert args['headers'] == {'Authorization':'Bearer '+CANARY}
    assert args['json']['model'] == MODEL and args['json']['n'] == 1
    assert args['json']['quality'] == 'low' and args['json']['size'] == '1024x1024'
    assert CANARY not in json.dumps(args['json'])
    again, data = asyncio.run(generator.generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert again.reason == 'image_attempt_limit' and data is None and len(http.calls) == 1


@pytest.mark.parametrize('budget,reason', [('0','image_generation_disabled'), ('0.001','image_budget_insufficient')])
def check_image_budget_stops_before_network(budget,reason):
    http = HTTP()
    result, data = asyncio.run(tool(http,budget).generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert result.reason == reason and data is None and not http.calls


@pytest.mark.parametrize('status,reason', [(401,'image_authentication'),(403,'image_permission'),
                                       (429,'image_rate_or_quota_limit'),(500,'image_provider_error')])
def check_errors_do_not_retry_or_leak(status,reason):
    http = HTTP(CANARY.encode(),status)
    result, data = asyncio.run(tool(http).generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert result.reason == reason and data is None and len(http.calls) == 1
    assert result.accounting == 'reserved' and CANARY not in result.model_dump_json()


@pytest.mark.parametrize('body', [b'not json', b'{"data":[{"url":"https://attacker.invalid/image"}]}',
                                b'{"data":[{"b64_json":"not base64"}]}', b'{"data":[]}'])
def check_invalid_images_never_download_external_urls(body):
    http = HTTP(body)
    result, data = asyncio.run(tool(http).generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert result.reason == 'image_invalid_response' and data is None and len(http.calls) == 1


def check_response_size_is_bounded():
    http = HTTP(b'x'*12_000_001)
    result, data = asyncio.run(tool(http).generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert result.reason == 'image_response_too_large' and data is None


def check_secret_guard_prevents_image_prompt_leakage():
    http = HTTP()
    with pytest.raises(ValueError):
        asyncio.run(tool(http).generate_product_image(ProductImageRequest(product=CANARY)))
    assert not http.calls


def check_image_tool_schema_cannot_accept_keys_urls_or_settings():
    with pytest.raises(ValueError):
        ProductImageRequest(product=PRODUCT, api_key=CANARY, url='https://attacker.invalid', n=100)


@pytest.mark.parametrize('budget', ['NaN','Infinity','-1','2'])
def check_invalid_image_budgets(budget):
    with pytest.raises(ValueError): image_budget(budget)


def check_timeout_does_not_retry():
    class TimeoutStream(Stream):
        async def __aenter__(self): raise TimeoutError(CANARY)
    http = HTTP()
    http.reply = TimeoutStream(b'')
    result, data = asyncio.run(tool(http).generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert result.reason == 'image_timeout' and len(http.calls) == 1 and data is None


def check_provider_usage_over_budget_is_reported():
    http = HTTP(json.dumps({'data':[{'b64_json':base64.b64encode(PNG).decode()}],
                           'usage':{'input_tokens':100,'output_tokens':2000}}).encode())
    result, data = asyncio.run(tool(http).generate_product_image(ProductImageRequest(product=PRODUCT)))
    assert result.budget_exceeded and result.accounting == 'provider_usage' and data == PNG
