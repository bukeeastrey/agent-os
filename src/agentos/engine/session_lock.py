"""Per-session write locks to prevent concurrent modifications."""

from __future__ import annotations

import asyncio
from typing import Any


class SessionWriteLock:
    """Async lock per session_key to serialize writes."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiters: dict[str, int] = {}

    async def acquire(self, session_key: str) -> None:
        """Acquire the lock for a session."""
        if session_key not in self._locks:
            self._locks[session_key] = asyncio.Lock()
            self._waiters[session_key] = 0
        self._waiters[session_key] += 1
        try:
            await self._locks[session_key].acquire()
        except BaseException:
            self._waiters[session_key] -= 1
            if self._waiters[session_key] <= 0 and not self._locks[session_key].locked():
                self._locks.pop(session_key, None)
                self._waiters.pop(session_key, None)
            raise

    def release(self, session_key: str) -> None:
        """Release the lock for a session and evict unreferenced locks."""
        if session_key in self._locks:
            lock = self._locks[session_key]
            lock.release()
            self._waiters[session_key] -= 1
            if self._waiters[session_key] <= 0 and not lock.locked():
                self._locks.pop(session_key, None)
                self._waiters.pop(session_key, None)

    async def __aenter__(self) -> SessionWriteLock:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def context(self, session_key: str) -> SessionLockContext:
        """Return a context manager for the session lock."""
        return SessionLockContext(self, session_key)

    def __len__(self) -> int:
        return len(self._locks)


class SessionLockContext:
    """Context manager for session write lock."""

    def __init__(self, lock_manager: SessionWriteLock, session_key: str) -> None:
        self._lock_manager = lock_manager
        self._session_key = session_key

    async def __aenter__(self) -> None:
        await self._lock_manager.acquire(self._session_key)

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._lock_manager.release(self._session_key)
