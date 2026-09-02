"""Tests for SessionWriteLock memory eviction and mutual exclusion."""

from __future__ import annotations

import asyncio

import pytest

from agentos.engine.session_lock import SessionWriteLock


@pytest.mark.asyncio
async def test_session_write_lock_evicts_idle_keys() -> None:
    lock_mgr = SessionWriteLock()
    for i in range(50):
        session_key = f"session-{i}"
        await lock_mgr.acquire(session_key)
        assert len(lock_mgr) == 1
        lock_mgr.release(session_key)
        assert len(lock_mgr) == 0

    assert len(lock_mgr) == 0


@pytest.mark.asyncio
async def test_session_write_lock_context_manager_prunes_on_exit() -> None:
    lock_mgr = SessionWriteLock()
    async with lock_mgr.context("session-ctx"):
        assert len(lock_mgr) == 1
    assert len(lock_mgr) == 0


@pytest.mark.asyncio
async def test_session_write_lock_concurrent_waiters_retained_until_last_release() -> None:
    lock_mgr = SessionWriteLock()
    order: list[str] = []

    async def task_one() -> None:
        async with lock_mgr.context("shared-session"):
            order.append("t1_start")
            await asyncio.sleep(0.05)
            order.append("t1_done")

    async def task_two() -> None:
        await asyncio.sleep(0.01)
        async with lock_mgr.context("shared-session"):
            order.append("t2_start")
            await asyncio.sleep(0.01)
            order.append("t2_done")

    await asyncio.gather(task_one(), task_two())

    assert order == ["t1_start", "t1_done", "t2_start", "t2_done"]
    assert len(lock_mgr) == 0


@pytest.mark.asyncio
async def test_session_write_lock_waiter_cancelled_cleans_up() -> None:
    lock_mgr = SessionWriteLock()

    await lock_mgr.acquire("cancel-session")
    assert len(lock_mgr) == 1

    async def waiter() -> None:
        await lock_mgr.acquire("cancel-session")

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Release initial holder; lock must be completely evicted
    lock_mgr.release("cancel-session")
    assert len(lock_mgr) == 0
