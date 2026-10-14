"""Cancelling a waiting refresh must not strand the cross-process login lock."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

from beakr_sdk import AsyncBeakr
from beakr_sdk.credentials import Credential, RefreshLock, StoredTokens


@pytest.mark.asyncio
async def test_cancelling_refresh_while_waiting_releases_the_eventual_file_lock(
    tmp_path: Path, monkeypatch
) -> None:
    fcntl = pytest.importorskip("fcntl")
    path = tmp_path / "refresh.lock"
    holder = RefreshLock(path)
    holder.acquire()
    started, acquired = threading.Event(), threading.Event()

    class ObservedLock(RefreshLock):
        def acquire(self) -> None:
            started.set()
            super().acquire()
            acquired.set()

    waiter = ObservedLock(path)
    monkeypatch.setattr("beakr_sdk.async_client.RefreshLock", lambda: waiter)
    client = AsyncBeakr(base_url="https://api.test")
    client._credential = Credential(
        source="cli_tokens",
        tokens=StoredTokens(access_token="test-access", refresh_token="test-refresh"),
    )

    async def no_network() -> None:
        pass

    monkeypatch.setattr(client, "_refresh_now", no_network)
    task = asyncio.create_task(client._refresh("test-access"))
    try:
        assert await asyncio.to_thread(started.wait, 2), "refresh did not wait for the lock"
        task.cancel()
        await asyncio.sleep(0)  # Deliver cancellation while flock is still blocked.
        holder.release()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert await asyncio.to_thread(acquired.wait, 2), "lock worker did not finish"
        # to_thread keeps running after its awaiter is cancelled. The current
        # try/finally begins only after the await, so its raw fd stays locked.
        fd = os.open(path, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pytest.fail("Cancelled refresh left the shared credential lock held")
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    finally:
        holder.release()
        # Clean up even on the buggy implementation: never leave a test lock held.
        await asyncio.to_thread(acquired.wait, 2)
        waiter.release()
        await client.close()
