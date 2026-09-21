"""Discover and call the scraper through the official MCP SDK."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def run(args):
    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    async with httpx2.AsyncClient(headers={"X-API-Key": key}, timeout=180, trust_env=False) as http:
        async with Client(streamable_http_client(args.endpoint, http_client=http), read_timeout_seconds=180) as client:
            tools = await client.list_tools()
            if not any(tool.name == "scrape_html" for tool in tools.tools):
                raise RuntimeError("Server did not advertise scrape_html")
            result = await client.call_tool("scrape_html", {"url": args.url, "wait_seconds": 5, "timeout_seconds": 30})
            if result.is_error:
                print(result.content[0].text)
                return 1
            args.output_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%fZ")
            path = args.output_dir / f"scrape_mcp_{stamp}_{uuid.uuid4().hex}.json"
            with path.open("x", encoding="utf-8") as output:
                json.dump(result.structured_content, output, ensure_ascii=False, indent=2)
            print(f"MCP {client.protocol_version}: saved {path}")
            return 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--key-file", type=Path, default=root / "secrets/api_key.txt")
    parser.add_argument("--output-dir", type=Path, default=root / "outputs")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except Exception:
        raise SystemExit("MCP client failed. Check endpoint, API key, dependencies and server logs.") from None
