"""The cross-process refresh lock is bounded and never stranded (audit C055).

Root cause: the async client awaited ``asyncio.to_thread(lock.acquire)`` outside
its try/finally. Cancelling the awaiter left the worker thread blocked in
``flock``; when it later acquired the lock nothing released it, so every other
process sharing the login blocked until this one exited.
"""

from __future__ import annotations

import asyncio
import gc
import os
import threading
from pathlib import Path

import pytest

from beakr_sdk import async_client
from beakr_sdk.credentials import RefreshLock, RefreshLockTimeout

fcntl = pytest.importorskip("fcntl")


def _free(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def test_acquire_gives_up_after_its_timeout(tmp_path):
    path = tmp_path / "refresh.lock"
    holder = RefreshLock(path)
    holder.acquire()
    try:
        waiter = RefreshLock(path)
        with pytest.raises(RefreshLockTimeout):
            waiter.acquire(timeout=0.1)
        assert not waiter.held
    finally:
        holder.release()
    assert _free(path)


def test_the_context_manager_releases(tmp_path):
    path = tmp_path / "refresh.lock"
    with RefreshLock(path) as lock:
        assert lock.held and not _free(path)
    assert _free(path)


def test_a_lost_lock_object_releases_its_flock(tmp_path):
    path = tmp_path / "refresh.lock"
    lock = RefreshLock(path)
    lock.acquire()
    del lock
    gc.collect()
    assert _free(path)


@pytest.mark.asyncio
async def test_a_second_cancel_still_releases_a_late_acquisition(tmp_path):
    path = tmp_path / "refresh.lock"
    holder = RefreshLock(path)
    holder.acquire()
    acquired = threading.Event()

    class Observed(RefreshLock):
        def acquire(self, timeout=5.0):
            super().acquire(timeout)
            acquired.set()

    acquisition = async_client._LockAcquisition(Observed(path))
    task = asyncio.create_task(acquisition.wait())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()  # cancelled again while waiting for the thread to finish
    with pytest.raises(asyncio.CancelledError):
        await task
    holder.release()
    assert await asyncio.to_thread(acquired.wait, 2)
    for _ in range(100):  # the worker releases right after acquiring
        if _free(path):
            break
        await asyncio.sleep(0.01)
    assert _free(path)


@pytest.mark.asyncio
async def test_an_uncancelled_acquisition_is_held_until_abandoned(tmp_path):
    path = tmp_path / "refresh.lock"
    acquisition = async_client._LockAcquisition(RefreshLock(path))
    await acquisition.wait()
    assert not _free(path)
    acquisition.abandon()
    assert _free(path)
    acquisition.abandon()  # idempotent
