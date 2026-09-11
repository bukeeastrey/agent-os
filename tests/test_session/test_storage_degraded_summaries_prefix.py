"""Tests for list_degraded_summaries LIKE wildcard escaping (#1771).

Pins the contract that wildcard characters like '_' and '%' in session_key_prefix
are escaped and matched literally, rather than acting as SQL pattern wildcards.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentos.session.models import SessionNode, SessionSummary
from agentos.session.storage import SessionStorage, _escape_like


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


def test_escape_like_helper() -> None:
    assert _escape_like("plain_text") == "plain\\_text"
    assert _escape_like("100%") == "100\\%"
    assert _escape_like("path\\to") == "path\\\\to"
    assert _escape_like("mix_%\\") == "mix\\_\\%\\\\"
    assert _escape_like("normal-key:123") == "normal-key:123"


@pytest.mark.asyncio
async def test_list_degraded_summaries_escapes_underscore_wildcard(
    storage: SessionStorage,
) -> None:
    # Seed sessions and degraded summaries
    # session_1_test has literal underscore after session
    # session1_test has digit '1' at that position (would match unescaped 'session_%')
    # session_a_test has literal underscore after session
    keys_and_ids = [
        ("session_1_test", "sid-1"),
        ("session1_test", "sid-2"),
        ("session_a_test", "sid-3"),
    ]

    for key, sid in keys_and_ids:
        await storage.upsert_session(
            SessionNode(
                session_key=key,
                session_id=sid,
                created_at=1000,
                updated_at=1000,
            )
        )
        summary = SessionSummary(
            session_id=sid,
            session_key=key,
            compaction_id=f"cmp-{sid}",
            compaction_index=0,
            summary_text=f"summary for {key}",
            flush_receipt_status="degraded_forensic",
        )
        await storage.save_summary(summary)

    # Query with prefix "session_"
    results = await storage.list_degraded_summaries(session_key_prefix="session_")
    matched_keys = [s.session_key for s in results]

    # Should match "session_1_test" and "session_a_test", but NOT "session1_test"
    assert "session_1_test" in matched_keys
    assert "session_a_test" in matched_keys
    assert "session1_test" not in matched_keys
    assert len(matched_keys) == 2


@pytest.mark.asyncio
async def test_list_degraded_summaries_escapes_percent_wildcard(
    storage: SessionStorage,
) -> None:
    keys_and_ids = [
        ("session%special", "sid-pct"),
        ("sessionXspecial", "sid-other"),
    ]

    for key, sid in keys_and_ids:
        await storage.upsert_session(
            SessionNode(
                session_key=key,
                session_id=sid,
                created_at=1000,
                updated_at=1000,
            )
        )
        summary = SessionSummary(
            session_id=sid,
            session_key=key,
            compaction_id=f"cmp-{sid}",
            compaction_index=0,
            summary_text=f"summary for {key}",
            flush_receipt_status="failed_retryable",
        )
        await storage.save_summary(summary)

    # Query with prefix "session%"
    results = await storage.list_degraded_summaries(session_key_prefix="session%")
    matched_keys = [s.session_key for s in results]

    assert matched_keys == ["session%special"]
