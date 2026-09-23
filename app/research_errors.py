"""Allowlisted diagnostics only: never serialize raw exception/provider text."""
MESSAGES = {
    'product_evidence_budget': 'Product identities, offers and relevant visible evidence cannot fit the remaining input budget; no extraction request was sent.',
    'model_call_budget': 'Model call limit reached.',
    'model_input_budget': 'The conservative input estimate exceeds the remaining token budget; this request was not sent to OpenAI.',
    'model_output_budget': 'Insufficient remaining model output tokens.',
    'model_cost_budget': 'The next model request would exceed the configured USD cap.',
    'model_usage_invalid': 'Provider usage was missing or inconsistent; the full reservation was retained.',
    'provider_authentication': 'OpenAI returned HTTP 401; check the API credential.',
    'provider_permission': 'OpenAI returned HTTP 403; check project/model permissions.',
    'provider_quota': 'OpenAI reported a billing, spend or quota limit; no automatic retry was made.',
    'provider_bad_request': 'OpenAI rejected the request format or parameters (HTTP 400).',
    'provider_not_found': 'OpenAI returned HTTP 404; check model availability for this project.',
    'provider_rate_limit': 'OpenAI rate-limit retry allowance was exhausted.',
    'provider_unavailable': 'OpenAI returned a server error.',
    'provider_http': 'OpenAI returned an unexpected HTTP status.',
    'provider_retry_delay': 'Provider retry delay is unsupported or exceeds the permitted wait.',
    'provider_response_size': 'The provider response exceeded the allowed size.',
    'provider_incomplete': 'The model did not complete its response.',
    'provider_output_limit': 'The model reached its per-call output limit before completing the decision.',
    'provider_timeout': 'The OpenAI request timed out.',
    'provider_connection': 'Could not reach OpenAI; check network, TLS and firewall settings.',
    'navigation_operation_budget': 'Navigation operation budget exhausted.',
    'navigation_link_budget': 'Followed-link budget exhausted.',
    'navigation_retry_budget': 'Scraper busy-response retry allowance exhausted.',
    'navigation_retry_delay': 'Scraper retry delay is unsupported.',
    'research_timeout': 'The research deadline was reached.',
    'unexpected_failure': 'An unexpected failure occurred; inspect the safe stage and counters.',
}


class ResearchFailure(RuntimeError):
    def __init__(self, code, *, http_status=None):
        self.code = code if code in MESSAGES else 'unexpected_failure'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__(MESSAGES[self.code])


class BudgetExceeded(ResearchFailure):
    pass


class ProviderFailure(ResearchFailure):
    pass


def failure_details(error, stage):
    code = error.code if isinstance(error, ResearchFailure) else (
        'research_timeout' if isinstance(error, TimeoutError) else 'unexpected_failure')
    details = {'code': code, 'stage': stage}
    if isinstance(error, ResearchFailure) and error.http_status is not None:
        details['http_status'] = error.http_status
    return details, f'Research stopped [{code}]: {MESSAGES[code]}'
