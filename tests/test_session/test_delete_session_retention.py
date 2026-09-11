"""Tests for delete_session safety retention option (#1772).

Pins the contract for retain_compacted on delete_session across storage,
manager, and gateway RPC.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentos.session.models import (
    SessionNode,
    SessionSummary,
    TranscriptEntry,
)
from agentos.session.storage import SessionStorage


@pytest.fixture
async def storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test.db"
        store = SessionStorage(str(path))
        await store.connect()
        try:
            yield store
        finally:
            await store.close()


async def _seed_session_with_compacted(
    storage: SessionStorage, session_key: str, session_id: str
) -> None:
    node = SessionNode(
        session_key=session_key,
        session_id=session_id,
        created_at=1000,
        updated_at=1000,
    )
    await storage.upsert_session(node)

    # Active transcript entry
    await storage.append_transcript_entry(
        TranscriptEntry(
            session_id=session_id,
            session_key=session_key,
            message_id="msg-active",
            role="user",
            content="active message",
            created_at=1001,
        )
    )

    # Summary
    summary = SessionSummary(
        session_id=session_id,
        session_key=session_key,
        compaction_id="cmp-1",
        compaction_index=0,
        summary_text="prior discussion summary",
    )
    await storage.save_summary(summary)

    # Compacted archived entries
    await storage._archive_transcript_entries(
        node=node,
        entries=[
            TranscriptEntry(
                id=1,
                session_id=session_id,
                session_key=session_key,
                message_id="msg-compacted-1",
                role="user",
                content="compacted historical message",
                created_at=999,
            )
        ],
        compaction_id="cmp-1",
        compaction_index=0,
    )
    await storage.conn.commit()


@pytest.mark.asyncio
async def test_delete_session_default_purges_all(storage: SessionStorage) -> None:
    key = "agent:main:webchat:direct:user-1"
    sid = "sid-purge"
    await _seed_session_with_compacted(storage, key, sid)

    # Default retain_compacted=False
    await storage.delete_session(key)

    assert await storage.get_session(key) is None
    assert await storage.get_transcript(sid) == []

    async with storage.conn.execute(
        "SELECT COUNT(*) FROM compacted_transcript_entries WHERE session_id = ?",
        (sid,),
    ) as cur:
        assert (await cur.fetchone())[0] == 0

    assert len(await storage.get_all_summaries(sid)) == 0


@pytest.mark.asyncio
async def test_delete_session_retain_compacted_preserves_archive(
    storage: SessionStorage,
) -> None:
    key = "agent:main:webchat:direct:user-2"
    sid = "sid-retain"
    await _seed_session_with_compacted(storage, key, sid)

    # With retain_compacted=True
    await storage.delete_session(key, retain_compacted=True)

    # Session and active transcripts are purged
    assert await storage.get_session(key) is None
    assert await storage.get_transcript(sid) == []

    # Compacted archive and summaries are preserved for historical audit
    async with storage.conn.execute(
        "SELECT COUNT(*) FROM compacted_transcript_entries WHERE session_id = ?",
        (sid,),
    ) as cur:
        assert (await cur.fetchone())[0] == 1

    summaries = await storage.get_all_summaries(sid)
    assert len(summaries) == 1
    assert summaries[0].summary_text == "prior discussion summary"


@pytest.mark.asyncio
async def test_session_manager_delete_forwards_retain_compacted(
    storage: SessionStorage,
) -> None:
    from agentos.session.manager import SessionManager

    manager = SessionManager(storage)
    key = "agent:main:webchat:direct:user-3"
    sid = "sid-mgr"
    await _seed_session_with_compacted(storage, key, sid)

    await manager.delete(key, retain_compacted=True)

    assert await storage.get_session(key) is None
    assert await storage.get_transcript(sid) == []

    async with storage.conn.execute(
        "SELECT COUNT(*) FROM compacted_transcript_entries WHERE session_id = ?",
        (sid,),
    ) as cur:
        assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_rpc_sessions_delete_supports_retain_compacted(storage: SessionStorage) -> None:
    from agentos.gateway.rpc_sessions import _handle_sessions_delete

    key = "agent:main:webchat:direct:user-4"
    sid = "sid-rpc"
    await _seed_session_with_compacted(storage, key, sid)

    from agentos.session.manager import SessionManager

    class Ctx:
        session_storage = storage
        session_manager = SessionManager(storage)
        task_runtime = None

    result = await _handle_sessions_delete(
        {"key": key, "retain_compacted": True},
        Ctx(),  # type: ignore[arg-type]
    )
    assert result == {"deleted": [key], "errors": []}

    assert await storage.get_session(key) is None
    async with storage.conn.execute(
        "SELECT COUNT(*) FROM compacted_transcript_entries WHERE session_id = ?",
        (sid,),
    ) as cur:
        assert (await cur.fetchone())[0] == 1
