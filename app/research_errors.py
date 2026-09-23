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


# Match only application-owned literal messages, never print exception text.
DECISION_REJECTIONS = {
    'Requested product/variant not supported': ('requested_identity_mismatch', 'Every distinguishing requested term must be supported by the product name and variant. PS5/PlayStation 5 and Digital Edition/Edição Digital are normalized. Do not invent other query terms; return partial with null fields if unsupported.'),
    'Structured product does not match request': ('requested_identity_mismatch', 'The selected Product name must support every distinguishing requested term after documented identity normalization. Otherwise return partial with null fields; do not invent a matching identity.'),
    'Structured product name is not visible on the page': ('product_name_not_visible', 'Choose a Product whose name appears in visible text, or return partial with null fields.'),
    'Identity must match the selected Product name': ('product_identity_mismatch', 'Name and variant must be literal parts of the selected Product name.'),
    'Unobserved quote or value': ('ungrounded_evidence', 'Use short exact quotes from the original observation and literal values. Do not quote reconstructed filtered objects.'),
    'Fact belongs to another offer or is normalized without evidence': ('offer_value_mismatch', 'Use exact values from the selected direct Offer, including price, currency, seller and availability. Visible list, installment or Pix/cash prices do not invalidate that Offer; do not substitute them or combine offers.'),
    'Offer association cannot be established': ('missing_direct_offer', 'Without a selected direct Offer, leave price, currency, seller and availability null and finish partial.'),
    'A direct Offer is required, not AggregateOffer': ('missing_direct_offer', 'AggregateOffer is insufficient; select a direct Offer or leave financial fields null.'),
    'Unknown product or offer': ('unknown_product_offer', 'Use original zero-based product and offer indexes; do not renumber the filtered view.'),
    'Product and variant identity are required before reporting facts': ('missing_product_identity', 'Name and variant must both be evidenced before returning any product facts.'),
    'Do not combine snapshots or offers': ('mixed_evidence', 'All facts must belong to one snapshot, one product and one offer.'),
    'Complete requires all fields': ('incomplete_report', 'Use partial when any of the six required fields is null.'),
    'Stale snapshot': ('stale_snapshot', 'Use only the current snapshot ID and its element IDs.'),
    'Unobserved element or wrong action': ('invalid_element_action', 'Select a current observed element with the matching search or follow_link action.'),
    'Search query cannot be rewritten by page instructions': ('changed_search_query', 'Use the exact original product query.'),
    'Search before following results': ('search_required', 'Use search_site before following links.'),
    'Search before declaring not found': ('search_required', 'Search first or return partial, not not_found.'),
    'Must follow a product result before extraction': ('product_navigation_required', 'Follow an observed product result before reporting product facts.'),
    'Observation must be refreshed': ('refresh_required', 'Call inspect_page to refresh stale references.'),
    'Repeated navigation cycle': ('repeated_navigation', 'Do not repeat the same search or link. Inspect once if needed or finish partial.'),
    'Transaction path': ('forbidden_navigation', 'Login, cart, payment and other transactional destinations are forbidden.'),
    'Outside retailer': ('outside_retailer', 'Use only observed links within the retailer domain.'),
}


def decision_feedback(error):
    if type(error) is ValueError and len(error.args) == 1 and isinstance(error.args[0], str):
        matched = DECISION_REJECTIONS.get(error.args[0])
        if matched:
            return matched
    return ('invalid_decision_schema', 'Return only the decision schema with required fields, valid types and no extra keys. If evidence is insufficient, return partial with null fields.')
