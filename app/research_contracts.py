"""Strict provider-neutral decisions and public retail reports."""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUIRED_FIELDS = ('name', 'variant', 'price', 'currency', 'seller', 'availability')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Fact(Strict):
    value: str = Field(min_length=1, max_length=1000)
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    quote: str = Field(min_length=1, max_length=2000)


class Fields(Strict):
    name: Fact | None
    variant: Fact | None
    price: Fact | None
    currency: Fact | None
    seller: Fact | None
    availability: Fact | None


class Report(Strict):
    status: Literal['complete', 'partial', 'not_found', 'blocked']
    fields: Fields
    # Indices reference the actual Product and its direct Offer in the snapshot.
    product_index: int | None = Field(ge=0, le=19)
    offer_index: int | None = Field(ge=0, le=99)
    reason: str | None = Field(max_length=2000)


class SearchArgs(Strict):
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    element_id: str = Field(pattern=r'^e[0-9]{1,3}$')
    query: str = Field(min_length=1, max_length=200, pattern=r'^[^\x00-\x1f\x7f]+$')


class LinkArgs(Strict):
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    element_id: str = Field(pattern=r'^e[0-9]{1,3}$')


class InspectArgs(Strict):
    pass


class SearchDecision(Strict):
    tool: Literal['search_site']
    arguments: SearchArgs


class LinkDecision(Strict):
    tool: Literal['follow_link']
    arguments: LinkArgs


class InspectDecision(Strict):
    tool: Literal['inspect_page']
    arguments: InspectArgs


class FinalDecision(Strict):
    report: Report


class PageAssessment(Strict):
    """Agent-authored assessment of the page in the current snapshot."""
    has_ps5_info: bool
    product_name: str | None = Field(min_length=1, max_length=1000)
    description: str | None = Field(min_length=1, max_length=5000)
    image_id: str | None = Field(pattern=r'^i[0-9]{1,2}$')

    @model_validator(mode='after')
    def consistent(self):
        values = (self.product_name, self.description, self.image_id)
        if not self.has_ps5_info and any(value is not None for value in values):
            raise ValueError('A negative page assessment must contain null product fields')
        if self.has_ps5_info and (self.product_name is None or self.description is None):
            raise ValueError('A positive page assessment requires product name and description')
        return self


class DecisionEnvelope(Strict):
    assessment: PageAssessment
    decision: SearchDecision | LinkDecision | InspectDecision | FinalDecision


class Product(Strict):
    name: str | None
    variant: str | None
    price: str | None
    currency: str | None
    seller: str | None
    availability: str | None


class Evidence(Strict):
    quote: str
    source_url: str
    observed_at: str


class Result(Strict):
    status: Literal['complete', 'partial', 'not_found', 'blocked']
    product: Product
    evidence: dict[str, Evidence]
    reason: str | None


def empty_result(reason, status='partial'):
    return Result(status=status, product=Product(**dict.fromkeys(REQUIRED_FIELDS)),
                  evidence={}, reason=reason).model_dump()


def tokens(value):
    return set(re.findall(r'\w+', value.casefold()))


def validate_report(candidate, snapshots, requested_product=None):
    """Conservative grounding: one snapshot, one Product, one direct Offer.

    Unstructured pages can support identity fields only. Financial facts require
    an explicit structured Offer; this deliberately trades recall for precision.
    JSON-LD is still untrusted and semantic correctness requires model evals.
    """
    report = Report.model_validate(candidate)
    facts = {k: getattr(report.fields, k) for k in REQUIRED_FIELDS}
    present = {k: v for k, v in facts.items() if v is not None}
    if report.status in ('not_found', 'blocked') and present:
        raise ValueError('Negative result cannot contain product facts')
    if report.status == 'complete' and len(present) != len(REQUIRED_FIELDS):
        raise ValueError('Complete requires all fields')
    if not present:
        return empty_result(report.reason, report.status)
    if not facts['name'] or not facts['variant']:
        raise ValueError('Product and variant identity are required before reporting facts')
    ids = {v.snapshot_id for v in present.values()}
    if len(ids) != 1:
        raise ValueError('Do not combine snapshots or offers')
    page = snapshots.get(next(iter(ids)))
    if page is None or not page.get('fetched_at') or page.get('status') == 'blocked':
        raise ValueError('Missing usable observation')
    observed = page['visible_text'] + '\n' + json.dumps(page.get('products', []), ensure_ascii=False)
    selected = None
    offer = None
    if report.product_index is not None:
        try:
            selected = page.get('products', [])[report.product_index]
            if not isinstance(selected, dict):
                raise ValueError('Invalid product')
            offers = selected.get('offers', [])
            offers = offers if isinstance(offers, list) else [offers]
            if report.offer_index is not None:
                offer = offers[report.offer_index]
                if not isinstance(offer, dict) or offer.get('@type') != 'Offer':
                    raise ValueError('A direct Offer is required, not AggregateOffer')
        except (IndexError, TypeError):
            raise ValueError('Unknown product or offer') from None
    elif report.offer_index is not None:
        raise ValueError('Offer without product')
    identity = facts['name'].value + ' ' + facts['variant'].value
    if requested_product and not tokens(requested_product) <= tokens(identity):
        raise ValueError('Requested product/variant not supported')
    if selected is not None:
        # Do not allow an accessory's description or another variant to establish identity.
        name = str(selected.get('name', ''))
        if name.casefold() not in page['visible_text'].casefold():
            raise ValueError('Structured product name is not visible on the page')
        if facts['name'].value.casefold() not in name.casefold() or facts['variant'].value.casefold() not in name.casefold():
            raise ValueError('Identity must match the selected Product name')
        if requested_product and not tokens(requested_product) <= tokens(name):
            raise ValueError('Structured product does not match request')
    field_keys = {'price': 'price', 'currency': 'priceCurrency', 'seller': 'seller', 'availability': 'availability'}
    values, evidence = dict.fromkeys(REQUIRED_FIELDS), {}
    for key, fact in present.items():
        if fact.quote not in observed or fact.value.casefold() not in fact.quote.casefold():
            raise ValueError('Unobserved quote or value')
        if key in field_keys:
            if offer is None:
                raise ValueError('Offer association cannot be established')
            actual = offer.get(field_keys[key])
            if key == 'seller' and isinstance(actual, dict):
                actual = actual.get('name')
            if actual is None or fact.value != str(actual):
                raise ValueError('Fact belongs to another offer or is normalized without evidence')
        elif selected is None and fact.quote not in page['visible_text']:
            raise ValueError('Unstructured identity needs visible evidence')
        values[key] = fact.value
        evidence[key] = Evidence(quote=fact.quote, source_url=page['url'], observed_at=page['fetched_at'])
    if selected is None and requested_product:
        identity_quote_words = tokens(facts['name'].quote) | tokens(facts['variant'].quote)
        if identity_quote_words <= tokens(requested_product):
            # An echoed query is visible text, but does not establish a listing.
            # Return a safe partial directly rather than spend retries restating it.
            return empty_result('Only the search query was evidenced; no specific product identity or offer was established.')
    return Result(status=report.status, product=Product(**values), evidence=evidence,
                  reason=report.reason).model_dump()
