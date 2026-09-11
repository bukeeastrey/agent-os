"""Tests for idx_sessions_session_id index on sessions (#1770).

Pins the contract that sessions.session_id is indexed, preventing full table
scans during search_transcript when joining sessions on session_id for project filtering.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

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


@pytest.mark.asyncio
async def test_idx_sessions_session_id_exists(storage: SessionStorage) -> None:
    async with storage.conn.execute("PRAGMA index_list(sessions)") as cur:
        indexes = [row[1] for row in await cur.fetchall()]

    assert "idx_sessions_session_id" in indexes


@pytest.mark.asyncio
async def test_search_transcript_with_project_id_uses_index(
    storage: SessionStorage,
) -> None:
    # Seed 2 sessions in different projects
    session_a = SessionNode(
        session_key="agent:main:s1",
        session_id="sid-proj1",
        project_id="proj-alpha",
        created_at=1000,
        updated_at=1000,
    )
    session_b = SessionNode(
        session_key="agent:main:s2",
        session_id="sid-proj2",
        project_id="proj-beta",
        created_at=1000,
        updated_at=1000,
    )
    await storage.upsert_session(session_a)
    await storage.upsert_session(session_b)

    # Seed transcript entries
    await storage.append_transcript_entry(
        TranscriptEntry(
            session_id="sid-proj1",
            session_key="agent:main:s1",
            message_id="msg-1",
            role="user",
            content="alpha deployment report",
            created_at=1001,
        )
    )
    await storage.append_transcript_entry(
        TranscriptEntry(
            session_id="sid-proj2",
            session_key="agent:main:s2",
            message_id="msg-2",
            role="user",
            content="beta deployment report",
            created_at=1002,
        )
    )

    # Check query execution plan
    sql = (
        "EXPLAIN QUERY PLAN "
        "SELECT t.id, t.session_key, t.role, t.created_at, "
        "snippet(transcript_fts, 0, '>>>', '<<<', '...', 48) AS snippet "
        "FROM transcript_fts f "
        "JOIN transcript_entries t ON f.rowid = t.id "
        "JOIN sessions s ON s.session_id = t.session_id "
        "WHERE f.content MATCH ? AND s.project_id = ? "
        "ORDER BY f.rank LIMIT ?"
    )
    async with storage.conn.execute(sql, ("deployment", "proj-alpha", 20)) as cur:
        plan_rows = await cur.fetchall()

    plan_detail = " ".join(str(row[-1]) for row in plan_rows)
    # Ensure that sessions table join is indexed rather than a full table SCAN of s
    assert "idx_sessions_session_id" in plan_detail or "SEARCH s USING" in plan_detail
    assert "SCAN s" not in plan_detail

    # Check functional accuracy
    hits_alpha = await storage.search_transcript(query="deployment", project_id="proj-alpha")
    assert len(hits_alpha) == 1
    assert hits_alpha[0]["session_key"] == "agent:main:s1"

    hits_beta = await storage.search_transcript(query="deployment", project_id="proj-beta")
    assert len(hits_beta) == 1
    assert hits_beta[0]["session_key"] == "agent:main:s2"
