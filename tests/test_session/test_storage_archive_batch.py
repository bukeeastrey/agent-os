"""Tests for SessionStorage._archive_transcript_entries batching (#1773).

Pins the executemany batch insertion contract used during compaction archiving
to ensure N individual INSERT queries are not executed across the worker thread boundary.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agentos.session.models import SessionNode, TranscriptEntry
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


def _make_node(session_id: str = "sid-test") -> SessionNode:
    return SessionNode(
        session_key=f"agent:test:{session_id}",
        session_id=session_id,
        agent_id="test",
        status="idle",
        created_at=1,
        updated_at=1,
    )


def _make_entries(session_id: str, count: int) -> list[TranscriptEntry]:
    return [
        TranscriptEntry(
            id=i + 1,
            session_id=session_id,
            session_key=f"agent:test:{session_id}",
            message_id=f"msg-{i}",
            role="user" if i % 2 == 0 else "assistant",
            content=f"content {i}",
            created_at=1000 + i,
            token_count=10,
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_archive_transcript_entries_uses_executemany(storage: SessionStorage) -> None:
    node = _make_node()
    await storage.upsert_session(node)
    entries = _make_entries(node.session_id, 10)

    original_executemany = storage.conn.executemany
    original_execute = storage.conn.execute
    executemany_calls: list[tuple] = []
    execute_insert_calls: list[tuple] = []

    async def tracking_executemany(sql, params):
        executemany_calls.append((sql, list(params)))
        return await original_executemany(sql, params)

    async def tracking_execute(sql, *args, **kwargs):
        if "INSERT INTO compacted_transcript_entries" in str(sql):
            execute_insert_calls.append((sql, args, kwargs))
        return await original_execute(sql, *args, **kwargs)

    with (
        patch.object(storage.conn, "executemany", side_effect=tracking_executemany),
        patch.object(storage.conn, "execute", side_effect=tracking_execute),
    ):
        await storage._archive_transcript_entries(
            node=node,
            entries=entries,
            compaction_id="cmp-1773",
            compaction_index=0,
        )

    assert len(executemany_calls) == 1
    assert "INSERT INTO compacted_transcript_entries" in executemany_calls[0][0]
    assert len(executemany_calls[0][1]) == 10
    assert len(execute_insert_calls) == 0

    async with storage.conn.execute(
        "SELECT COUNT(*) FROM compacted_transcript_entries WHERE session_id = ?",
        (node.session_id,),
    ) as cur:
        count = (await cur.fetchone())[0]
    assert count == 10

    async with storage.conn.execute(
        "SELECT compaction_id, compaction_index, original_entry_id, content "
        "FROM compacted_transcript_entries WHERE session_id = ? ORDER BY original_entry_id ASC",
        (node.session_id,),
    ) as cur:
        rows = await cur.fetchall()

    for i, row in enumerate(rows):
        assert row[0] == "cmp-1773"
        assert row[1] == 0
        assert row[2] == i + 1
        assert row[3] == f"content {i}"


@pytest.mark.asyncio
async def test_archive_transcript_entries_empty_noop(storage: SessionStorage) -> None:
    node = _make_node()
    await storage.upsert_session(node)

    with patch.object(storage.conn, "executemany") as mock_executemany:
        await storage._archive_transcript_entries(
            node=node,
            entries=[],
            compaction_id="cmp-empty",
            compaction_index=0,
        )
        mock_executemany.assert_not_called()


@pytest.mark.asyncio
async def test_archive_transcript_entries_500_plus(storage: SessionStorage) -> None:
    node = _make_node("sid-large")
    await storage.upsert_session(node)
    entries = _make_entries(node.session_id, 550)

    await storage._archive_transcript_entries(
        node=node,
        entries=entries,
        compaction_id="cmp-large",
        compaction_index=1,
    )

    async with storage.conn.execute(
        "SELECT COUNT(*) FROM compacted_transcript_entries WHERE session_id = ?",
        (node.session_id,),
    ) as cur:
        count = (await cur.fetchone())[0]
    assert count == 550


@pytest.mark.asyncio
async def test_rewrite_compacted_session_archives_entries_batched(storage: SessionStorage) -> None:
    from agentos.session.models import SessionSummary

    node = _make_node("sid-compact-e2e")
    await storage.upsert_session(node)

    # Append 5 initial entries to storage
    initial_entries = _make_entries(node.session_id, 5)
    for entry in initial_entries:
        await storage.append_transcript_entry(entry)

    assert await storage.count_transcript_entries(node.session_id) == 5

    # Compact: 3 archived, 2 kept
    archived = initial_entries[:3]
    kept = initial_entries[3:]
    summary = SessionSummary(
        session_id=node.session_id,
        session_key=node.session_key,
        compaction_id="cmp-e2e",
        summary_text="Summarized prior 3 messages",
    )

    await storage.rewrite_compacted_session(
        node=node,
        summary=summary,
        entries=kept,
        archived_entries=archived,
    )

    # Kept entries in active transcript
    active_entries = await storage.get_transcript(node.session_id)
    assert len(active_entries) == 2
    assert [e.content for e in active_entries] == ["content 3", "content 4"]

    # Archived entries in compacted_transcript_entries
    async with storage.conn.execute(
        "SELECT compaction_id, compaction_index, original_entry_id, content "
        "FROM compacted_transcript_entries WHERE session_id = ? ORDER BY original_entry_id ASC",
        (node.session_id,),
    ) as cur:
        archived_rows = await cur.fetchall()

    assert len(archived_rows) == 3
    assert [r[3] for r in archived_rows] == ["content 0", "content 1", "content 2"]
    assert all(r[0] == "cmp-e2e" for r in archived_rows)
