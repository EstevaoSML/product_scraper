"""Connect your async decision adapter to the bounded retail research loop."""
import argparse
import asyncio
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(args):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from app.research import research
    key = Path(args.key_file).read_text(encoding='utf-8').strip()
    decide = importlib.import_module(args.decision_module).decide
    async with httpx2.AsyncClient(headers={'X-API-Key': key}, timeout=80) as http:
        async with Client(streamable_http_client(args.endpoint, http_client=http)) as client:
            report = await research(client, decide, args.url, args.product)
    destination = ROOT / 'outputs' / ('research-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json')
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8000/mcp')
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--decision-module', required=True, help='Trusted local Python module exposing async decide(messages)')
    parser.add_argument('--url', required=True)
    parser.add_argument('--product', required=True)
    asyncio.run(run(parser.parse_args()))
