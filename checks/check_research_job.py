"""Job integration with actual loop/adapter, fake external transports only."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from app import research_job
from evals.retail_fixtures import CANARY, PRODUCT, WEBSITE, SyntheticMCP, decisions


class Context:
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False


def setup_job(monkeypatch):
    import httpx
    import httpx2
    import mcp
    import mcp.client.streamable_http as stream
    from checks.check_research_openai import response
    client = SyntheticMCP()
    sequence = iter(decisions())
    recorded = {}
    class HTTP(Context):
        def __init__(self,**kwargs): recorded.setdefault('http_options',[]).append(kwargs)
        async def post(self,url,**kwargs):
            assert kwargs['headers'] == {'Authorization':'Bearer '+CANARY}
            assert CANARY not in json.dumps(kwargs['json'])
            return response(decision=next(sequence))
    class MCP(Context):
        def __init__(self,transport): assert transport=='transport'
        async def __aenter__(self): return client
    def transport(endpoint,**kwargs):
        assert endpoint == 'http://127.0.0.1:8000/mcp'
        return 'transport'
    monkeypatch.setattr(httpx,'AsyncClient',HTTP)
    monkeypatch.setattr(httpx2,'AsyncClient',HTTP)
    monkeypatch.setattr(mcp,'Client',MCP)
    monkeypatch.setattr(stream,'streamable_http_client',transport)
    monkeypatch.setenv('OPENAI_API_KEY',CANARY)
    monkeypatch.setenv('MCP_API_KEY','SYNTHETIC_MCP_CREDENTIAL')
    return client,recorded


def arguments(tmp_path,**kwargs):
    args = dict(url=WEBSITE,product=PRODUCT,max_cost_usd='0.05',key_file=None,
                endpoint='http://127.0.0.1:8000/mcp',storage_account=None,
                identity_client_id=None,output_dir=str(tmp_path))
    return SimpleNamespace(**(args|kwargs))


def check_local_job_entire_pipeline(monkeypatch,tmp_path,capsys):
    client,recorded = setup_job(monkeypatch)
    result = asyncio.run(research_job.run(arguments(tmp_path)))
    assert result['status']=='complete'
    saved = json.loads(next(tmp_path.glob('research-*.json')).read_text())
    assert saved==result
    assert client.calls[-1][0]=='close_session'
    assert any(o.get('headers',{}).get('X-API-Key')=='SYNTHETIC_MCP_CREDENTIAL' for o in recorded['http_options'])
    output = capsys.readouterr().out
    assert CANARY not in output and 'SYNTHETIC_MCP_CREDENTIAL' not in output
    assert json.loads(output)['navigation']['cleanup']=='closed'
    diagnostics = next(tmp_path.glob('diagnostics-*.json')).read_text()
    assert json.loads(diagnostics) == json.loads(output)
    assert CANARY not in diagnostics and 'SYNTHETIC_MCP_CREDENTIAL' not in diagnostics


def check_local_key_file(monkeypatch,tmp_path):
    setup_job(monkeypatch)
    key = tmp_path/'synthetic-key.txt'
    key.write_text('SYNTHETIC_MCP_CREDENTIAL')
    assert asyncio.run(research_job.run(arguments(tmp_path,key_file=str(key))))['status']=='complete'


def check_agent_default_directory_preserves_previous_runs(monkeypatch,tmp_path,capsys):
    monkeypatch.chdir(tmp_path)
    for _ in range(2):
        setup_job(monkeypatch)
        args = research_job.parser().parse_args([
            '--url',WEBSITE,'--product',PRODUCT,'--max-cost-usd','0.05'])
        assert args.output_dir == 'outputs/agent'
        # Isolate local CLI behavior from inherited Azure configuration.
        args.storage_account = None
        asyncio.run(research_job.run(args))
    folder = tmp_path/'outputs'/'agent'
    assert len(list(folder.glob('research-*.json'))) == 2
    assert len(list(folder.glob('diagnostics-*.json'))) == 2
    assert not list((tmp_path/'outputs').glob('*.json'))
    runs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert runs[0]['run_id'] != runs[1]['run_id']
    assert all(json.loads((tmp_path/run['result']).read_text())['status']=='complete' for run in runs)
    for report in folder.glob('research-*.json'):
        assert re.fullmatch(r'research-\d{8}T\d{12}Z-[a-f0-9]{32}\.json', report.name)
        assert (folder / report.name.replace('research-', 'diagnostics-', 1)).exists()


def check_large_pages_fit_budget_and_complete_with_cleanup(monkeypatch,tmp_path,capsys):
    client, _ = setup_job(monkeypatch)
    for page in client.pages:
        page['visible_text'] = 'Unrelated promotion\n' * 1000 + page['visible_text']
        page['elements'] += [dict(element_id=f'e{i+2}', action='follow_link',
            href=WEBSITE+str(i), accessible_name='Unrelated category '*8) for i in range(98)]
    result = asyncio.run(research_job.run(arguments(tmp_path)))
    assert result['status'] == 'complete'
    assert result['product']['seller'] == 'Loja Azul'
    assert [name for name, _ in client.calls] == ['open_page','search_site','follow_link','close_session']
    summary = json.loads(capsys.readouterr().out)
    assert summary['budgets']['calls'] == 3 and summary['navigation']['cleanup'] == 'closed'


def check_azure_job_saves_after_cleanup(monkeypatch,tmp_path):
    import azure.identity.aio as identity
    import azure.storage.blob.aio as blobs
    from azure.core.exceptions import ResourceExistsError
    client, _ = setup_job(monkeypatch)
    events = []
    class Lease:
        async def renew(self): pass
        async def release(self): events.append('release')
    class Lock:
        async def upload_blob(self,*args,**kwargs): raise ResourceExistsError('synthetic')
        async def acquire_lease(self,**kwargs):
            events.append('acquire')
            assert client.calls==[]
            return Lease()
    class Storage(Context):
        def __init__(self,url,**kwargs): assert url=='https://research123.blob.core.windows.net'
        def get_container_client(self,name):
            assert name=='research'
            return self
        def get_blob_client(self,name):
            assert name=='locks/browser'
            return Lock()
        async def upload_blob(self,name,payload,**kwargs):
            assert client.calls[-1][0]=='close_session'
            assert json.loads(payload)['status']=='complete'
            assert name.startswith('reports/')
            assert re.fullmatch(r'reports/\d{8}T\d{12}Z-[a-f0-9]{32}\.json', name)
            assert kwargs['overwrite'] is False
            events.append('upload')
    class Credential(Context):
        def __init__(self,**kwargs): assert kwargs=={'client_id':'agent-identity'}
    monkeypatch.setattr(identity,'ManagedIdentityCredential',Credential)
    monkeypatch.setattr(blobs,'BlobServiceClient',Storage)
    result = asyncio.run(research_job.run(arguments(tmp_path,storage_account='research123',identity_client_id='agent-identity')))
    assert result['status']=='complete' and events==['acquire','release','upload']


@pytest.mark.parametrize('option', ['identity','account','key'])
def check_job_invalid_configuration_does_not_open_browser(monkeypatch,tmp_path,option):
    client,_ = setup_job(monkeypatch)
    args=arguments(tmp_path)
    if option=='identity': args.storage_account='research123'
    if option=='account': args.storage_account='evil.invalid/'; args.identity_client_id='identity'
    if option=='key': monkeypatch.setenv('OPENAI_API_KEY','')
    with pytest.raises(ValueError): asyncio.run(research_job.run(args))
    assert not client.calls


def check_cli_requires_explicit_task_and_budget():
    with pytest.raises(SystemExit): research_job.parser().parse_args([])
    args=research_job.parser().parse_args(['--url',WEBSITE,'--product',PRODUCT,'--max-cost-usd','0.05'])
    assert args.max_cost_usd=='0.05'


def check_main_does_not_print_exception_secrets(monkeypatch,capsys):
    monkeypatch.setattr(research_job,'parser',lambda: SimpleNamespace(parse_args=lambda:None))
    async def fail(args): raise ValueError(CANARY)
    monkeypatch.setattr(research_job,'cancellable_run',fail)
    with pytest.raises(SystemExit): research_job.main()
    output=capsys.readouterr()
    assert CANARY not in output.out+output.err
    assert json.loads(output.out)['status']=='failed'


def check_cancellable_runner(monkeypatch):
    async def work(args): return 'result'
    monkeypatch.setattr(research_job,'run',work)
    assert asyncio.run(research_job.cancellable_run(None))=='result'
