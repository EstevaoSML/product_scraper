"""Bounded image generation, independent of retailer searches and their retries."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

IMAGE_CENTS = 5
MAX_IMAGES = 50


def validate_images(ledger):
    attempts = ledger.setdefault('images', {})
    if (not isinstance(attempts, dict) or len(attempts) > MAX_IMAGES
            or any(not isinstance(v, dict) or v.get('reserved_cents') != IMAGE_CENTS for v in attempts.values())):
        raise ValueError('Invalid image reservations')
    return attempts


def reserve_image(ledger, product_id):
    attempts = validate_images(ledger)
    if product_id in attempts or len(attempts) >= MAX_IMAGES:
        return False
    attempts[product_id] = dict(reserved_cents=IMAGE_CENTS, status='reserved',
                               at=datetime.now(timezone.utc).isoformat())
    return True


async def generate(seed, api_key):
    import httpx
    from app.research_images import ProductImageTool, ProductImageRequest
    from app.research_openai import SecretGuard
    # Same host-only image tool as research_job; no browser, retries or retailer data.
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        tool = ProductImageTool(client, api_key, '0.05', SecretGuard((api_key,)))
        result, data = await tool.generate_product_image(ProductImageRequest(product=seed['query']))
    return result.model_dump(), data


def ensure_image(container, blob, lease, lost, state, seed, ledger, save_ledger, directory, api_key):
    from webapp.cloud_job import add_image, publish
    product_id = seed['id']
    if state['runs'].get(product_id, {}).get('image_blob'):
        return
    if lost.is_set():
        raise RuntimeError('Writer lease lost')
    if not reserve_image(ledger, product_id):
        return
    save_ledger()  # Durable reservation before the paid request; never refunded.
    metadata, data = asyncio.run(generate(seed, api_key))
    if lost.is_set():
        raise RuntimeError('Writer lease lost')
    if data is not None:
        path = Path(directory) / (product_id + '.png')
        path.write_bytes(data)
        state['runs'].setdefault(product_id, dict(product_id=product_id, status='image_only'))
        add_image(container, state, product_id, path)
        state['runs'][product_id]['image_status'] = 'generated'
        publish(container, blob, lease, lost, state)
    ledger['images'][product_id].update(status=metadata['status'],
        accounted_usd=metadata['usd_accounted'], budget_exceeded=metadata['budget_exceeded'])
    save_ledger()
    if metadata['budget_exceeded']:
        # Image API has no server-side dollar cap: stop subsequent paid work.
        raise RuntimeError('Image estimate exceeded; inspect private ledger before continuing')

