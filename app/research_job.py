"""Manual Azure Container Apps Job entry point; also usable locally."""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import uuid
from urllib.parse import urlsplit

from app.research import PROMPT_VERSION, research, safe_retail_url
from app.research_openai import ModelBudget, OpenAIDecider, SecretGuard
from app.research_storage import exclusive_research


def unique_agent_assessments(assessments):
    """Keep URL order and prefer a positive assessment over an earlier negative."""
    payload, indexes = [], {}
    for assessment in assessments:
        entry = copy.deepcopy({key: value for key, value in assessment.items() if not key.startswith('_')})
        url = entry.get('url')
        if url not in indexes:
            indexes[url] = len(payload)
            payload.append(entry)
        elif not payload[indexes[url]].get('has_ps5_info') and entry.get('has_ps5_info'):
            payload[indexes[url]] = entry
    return payload


def save_agent_assessments(assessments, scrape_folder, file_id):
    """Persist the Agent's ordered per-page product assessments."""
    folder = Path(scrape_folder)
    folder.mkdir(parents=True, exist_ok=True)
    payload = unique_agent_assessments(assessments)
    destination = folder / f'scrape-{file_id}.json'
    with destination.open('x', encoding='utf-8') as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
    return destination


def validate_endpoint(endpoint):
    parsed = urlsplit(endpoint)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != '/mcp' or not parsed.hostname
            or (parsed.scheme != 'https' and not
                (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')))):
        raise ValueError('Use a trusted HTTPS MCP endpoint or local loopback /mcp')
    return endpoint


async def run(args):
    # Validate configuration before reading any credentials or making paid calls.
    safe_retail_url(args.url)
    validate_endpoint(args.endpoint)
    budget = ModelBudget(args.max_cost_usd)
    run_id = uuid.uuid4().hex
    run_timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    file_id = f'{run_timestamp}-{run_id}'
    mcp_key = Path(args.key_file).read_text(encoding='utf-8').strip() if args.key_file else os.environ.pop('MCP_API_KEY')
    model_key = os.environ.pop('OPENAI_API_KEY')
    if not mcp_key or not model_key:
        raise ValueError('Missing credentials')
    guard = SecretGuard((mcp_key, model_key))
    guard.check({'url': args.url, 'product': args.product, 'endpoint': args.endpoint})
    stats = {}
    assessments = []

    async def work():
        import httpx
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as model_http:
            decide = OpenAIDecider(model_http, model_key, budget, guard=guard)
            async with httpx2.AsyncClient(headers={'X-API-Key': mcp_key}, timeout=60,
                                           follow_redirects=False, trust_env=False) as mcp_http:
                async with Client(streamable_http_client(args.endpoint, http_client=mcp_http)) as client:
                    result = await research(client, decide, args.url, args.product, metrics=stats,
                                            on_assessment=assessments.append)
                    guard.check(result)
                    return result

    if args.storage_account:
        if not args.identity_client_id:
            raise ValueError('Explicit agent managed identity required')
        import re
        if not re.fullmatch(r'[a-z0-9]{3,24}', args.storage_account):
            raise ValueError('Invalid storage account')
        from azure.core.exceptions import ResourceExistsError
        from azure.identity.aio import ManagedIdentityCredential
        from azure.storage.blob.aio import BlobServiceClient
        async with ManagedIdentityCredential(client_id=args.identity_client_id) as credential:
            async with BlobServiceClient(f'https://{args.storage_account}.blob.core.windows.net',
                                         credential=credential, retry_total=0,
                                         connection_timeout=10, read_timeout=15) as storage:
                container = storage.get_container_client('research')
                lock = container.get_blob_client('locks/browser')
                try:
                    await asyncio.wait_for(lock.upload_blob(b'', overwrite=False), 20)
                except ResourceExistsError:
                    pass
                result = await exclusive_research(lock, work)
                payload = json.dumps(result, ensure_ascii=False)
                await asyncio.wait_for(container.upload_blob(f'reports/{file_id}.json', payload,
                                                              overwrite=False), 20)
                destination = f'reports/{file_id}.json'
                cloud_assessments = unique_agent_assessments(assessments)
                scrape_destination = f'scrapes/scrape-{file_id}.json'
                await asyncio.wait_for(container.upload_blob(
                    scrape_destination, json.dumps(cloud_assessments, ensure_ascii=False),
                    overwrite=False), 20)
    else:
        result = await work()
        folder = Path(args.output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f'research-{file_id}.json'
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        scrape_destination = save_agent_assessments(assessments, args.scrape_output_dir, file_id)
    # Only fixed metadata, never prompts, page bodies, request headers or SDK errors.
    summary = {'service': 'retail-agent', 'run_id': run_id, 'status': result['status'],
                      'prompt_version': PROMPT_VERSION, 'result': str(destination),
                      'budgets': budget.summary(), 'navigation': stats}
    summary['scrape_output'] = str(scrape_destination)
    if not args.storage_account:
        (folder / f'diagnostics-{file_id}.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))
    return result


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--url', required=True)
    cli.add_argument('--product', required=True)
    cli.add_argument('--max-cost-usd', required=True, help='Explicit per-run model cap, USD')
    cli.add_argument('--endpoint', default=os.getenv('MCP_ENDPOINT', 'http://127.0.0.1:8000/mcp'))
    cli.add_argument('--key-file', help='Local scraper key file; Azure uses MCP_API_KEY')
    cli.add_argument('--storage-account', default=os.getenv('RESEARCH_STORAGE_ACCOUNT'))
    cli.add_argument('--identity-client-id', default=os.getenv('AZURE_CLIENT_ID'))
    cli.add_argument('--output-dir', default='outputs/agent',
                     help='Local agent reports and diagnostics directory (default: outputs/agent)')
    cli.add_argument('--scrape-output-dir', default='outputs/scrapes',
                     help='Local MCP navigation observations directory (default: outputs/scrapes)')
    return cli


async def cancellable_run(args):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    installed = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        async with asyncio.timeout(270):
            return await run(args)
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


def main():
    # SDK debug logging must not accidentally print headers or request bodies.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(cancellable_run(parser().parse_args()))
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        print(json.dumps({'service': 'retail-agent', 'status': 'failed',
                          'reason': 'Job failed or cancelled; no raw exception is logged'}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
