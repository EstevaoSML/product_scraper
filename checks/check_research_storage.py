import asyncio

import pytest

from app.research_storage import exclusive_research
from app.research_job import validate_endpoint


class Lease:
    def __init__(self, fail=False): self.fail, self.released, self.renewed = fail, False, 0
    async def renew(self):
        self.renewed += 1
        if self.fail: raise RuntimeError('synthetic secret')
    async def release(self): self.released = True


class Blob:
    def __init__(self, lease, busy=False): self.lease, self.busy = lease, busy
    async def acquire_lease(self, **kwargs):
        assert kwargs['lease_duration'] == 60
        if self.busy: raise RuntimeError('busy')
        return self.lease


def check_lock_before_work_and_release_after_result():
    lease = Lease()
    async def work(): return 'result'
    assert asyncio.run(exclusive_research(Blob(lease), work)) == 'result'
    assert lease.released


def check_busy_lock_does_not_start_work():
    async def work(): raise AssertionError('Must not call MCP or model')
    with pytest.raises(RuntimeError): asyncio.run(exclusive_research(Blob(Lease(),busy=True), work))


@pytest.mark.parametrize('cause', ['renewal', 'cancel', 'timeout', 'work_error'])
def check_lease_failure_waits_for_work_finally(cause):
    async def scenario():
        lease = Lease(fail=cause=='renewal')
        finished, started = asyncio.Event(), asyncio.Event()
        async def work():
            try:
                started.set()
                if cause == 'work_error': raise ValueError('synthetic secret')
                await asyncio.Event().wait()
            finally: finished.set()
        task = asyncio.create_task(exclusive_research(Blob(lease),work,interval=0.001,
                                                       timeout=0.01 if cause=='timeout' else 1))
        if cause == 'cancel':
            await started.wait()
            task.cancel()
        with pytest.raises((RuntimeError,TimeoutError,ValueError,asyncio.CancelledError)): await task
        assert finished.is_set() and lease.released
    asyncio.run(scenario())


@pytest.mark.parametrize('url', ['http://evil.example/mcp','https://user:password@example.com/mcp',
                                'https://example.com/mcp?key=abc', 'https://example.com/other'])
def check_mcp_endpoint_does_not_leak_credentials(url):
    with pytest.raises(ValueError): validate_endpoint(url)


def check_local_and_azure_endpoints():
    assert validate_endpoint('http://127.0.0.1:8000/mcp')
    assert validate_endpoint('https://trusted.azurecontainerapps.io/mcp')
