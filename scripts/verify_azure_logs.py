"""Post-deploy proof: emit an unauthenticated rejection and find its request ID in Azure.

Does not need/read the scraper secret. Proves readiness and error-log transport,
not a successful authenticated retailer scrape.
"""
import argparse
import json
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import uuid


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = build_opener(ProxyHandler({}), NoRedirect())


def emit_error(endpoint):
    parts = urlsplit(endpoint)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443) or parts.path != '/mcp' or parts.query or parts.fragment:
        raise ValueError('Expected a plain HTTPS MCP endpoint')
    base = endpoint[:-4]
    with OPENER.open(base + '/readyz', timeout=30) as response:
        if json.load(response).get('status') != 'ready':
            raise RuntimeError('Deployment is not ready')
    try:
        OPENER.open(Request(base + '/scrape', data=b'{"url":"https://example.com"}',
                            headers={'Content-Type': 'application/json'}, method='POST'), timeout=30)
    except HTTPError as error:
        if error.code != 401:
            raise RuntimeError('Expected authentication rejection') from None
        body = json.loads(error.read(4096))
        request_id = str(uuid.UUID(body['request_id']))
        if body.get('code') != 'unauthorized':
            raise RuntimeError('Unexpected error contract')
        return request_id
    raise RuntimeError('Endpoint accepted an unauthenticated request')


def verify(endpoint, workspace, app_name, wait_seconds=600):
    workspace = str(uuid.UUID(workspace))
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,40}', app_name):
        raise ValueError('Invalid application name')
    request_id = emit_error(endpoint)
    query = (f'ContainerAppConsoleLogs_CL | where TimeGenerated > ago(15m) '
             f'| where ContainerAppName_s == "{app_name}" | extend e=parse_json(Log_s) '
             f'| where tostring(e.request_id) == "{request_id}" and tostring(e.severity) == "ERROR" '
             '| summarize Matches=count()')
    deadline = time.monotonic() + wait_seconds
    while True:
        result = subprocess.run(['az', 'monitor', 'log-analytics', 'query', '--workspace', workspace,
                                 '--analytics-query', query, '--timespan', 'PT15M', '-o', 'json'],
                                text=True, capture_output=True, timeout=60)
        if result.returncode == 0:
            rows = json.loads(result.stdout)
            if any(int(row.get('Matches', 0)) > 0 for row in rows):
                print('PASS: structured error received by Log Analytics; request ID ' + request_id)
                return
        if time.monotonic() >= deadline:
            raise RuntimeError('Error log was not confirmed in Log Analytics within the ingestion window; inspect workspace access and Container Apps log routing')
        time.sleep(min(30, max(0, deadline-time.monotonic())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--app-name', required=True)
    args = parser.parse_args()
    verify(args.endpoint, args.workspace, args.app_name)
