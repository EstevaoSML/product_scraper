"""Finite renewable Blob lease; one research at a time across job executions."""
import asyncio
from contextlib import suppress


async def exclusive_research(lock_blob, work, *, interval=20, timeout=240):
    """work is a factory: no MCP/model call occurs before lease acquisition.

    Stop work on renewal failure, await its finally, then release the lease.
    The server's browser lease is a second independent safety boundary.
    """
    lease = await asyncio.wait_for(lock_blob.acquire_lease(lease_duration=60), 15)
    task = renew = None

    async def renew_lease():
        while True:
            await asyncio.sleep(interval)
            await asyncio.wait_for(lease.renew(), 10)

    try:
        task = asyncio.create_task(work())
        renew = asyncio.create_task(renew_lease())
        done, _ = await asyncio.wait((task, renew), timeout=timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        if renew in done:
            # Retrieve failure without exposing SDK exception messages.
            with suppress(Exception):
                renew.result()
            raise RuntimeError('Research lease lost')
        if task not in done:
            raise TimeoutError('Job deadline')
        return await task
    finally:
        async def cleanup():
            for pending in (task, renew):
                if pending is not None and not pending.done():
                    pending.cancel()
            for pending in (task, renew):
                if pending is not None:
                    with suppress(asyncio.CancelledError, Exception):
                        await pending
            with suppress(Exception):
                await asyncio.wait_for(lease.release(), 10)
        closing = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            await closing
            raise
