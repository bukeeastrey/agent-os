"""delete_session must remove every session-scoped row, not just transcripts."""

from __future__ import annotations

from agentos.session.models import (
    AgentTaskRecord,
    AgentTaskStatus,
    MemoryDurableReceipt,
    SessionNode,
    TranscriptEntry,
)
from agentos.session.storage import SessionStorage

# Fixed epoch ms so the tests never read the system clock.
_T0 = 1_700_000_000_000

SESSION_KEY = "agent:main:webchat:direct:peer-1"


async def _seed(storage: SessionStorage, *, session_id: str, task_id: str) -> None:
    await storage.upsert_session(
        SessionNode(
            session_key=SESSION_KEY,
            session_id=session_id,
            created_at=_T0,
            updated_at=_T0,
        )
    )
    await storage.append_transcript_entry(
        TranscriptEntry(
            session_id=session_id,
            session_key=SESSION_KEY,
            message_id=f"{task_id}-msg",
            role="user",
            content="secret question",
            created_at=_T0,
        )
    )
    await storage.create_agent_task(
        AgentTaskRecord(
            task_id=task_id,
            session_key=SESSION_KEY,
            source_kind="webui",
            queue_mode="followup",
            run_kind="web_turn",
            status=AgentTaskStatus.SUCCEEDED,
            created_at=_T0,
            updated_at=_T0,
            details={"metadata": {"channel": "webchat"}},
        )
    )
    await storage.upsert_memory_durable_receipt(
        MemoryDurableReceipt(
            receipt_id=f"{task_id}-receipt",
            session_key=SESSION_KEY,
            session_id=session_id,
            turn_id="turn-1",
            scope="checkpoint",
            content_hash="h1",
            idempotency_key=f"checkpoint:{SESSION_KEY}:{task_id}",
            status="checkpoint_saved",
            created_at=_T0,
            updated_at=_T0,
        )
    )


async def test_delete_session_removes_tasks_and_memory_receipts() -> None:
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        await _seed(storage, session_id="session-1", task_id="task-1")

        await storage.delete_session(SESSION_KEY)

        assert await storage.count_sessions() == 0
        assert await storage.get_transcript("session-1") == []
        assert await storage.list_agent_tasks(SESSION_KEY) == []
        assert await storage.list_memory_durable_receipts(session_key=SESSION_KEY) == []
    finally:
        await storage.close()


async def test_recreated_session_key_does_not_inherit_deleted_tasks() -> None:
    """Session keys are deterministic, so a new chat reuses the deleted key."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        await _seed(storage, session_id="session-1", task_id="task-1")
        await storage.delete_session(SESSION_KEY)

        # Same key, brand-new session id — what the next inbound message creates.
        await storage.upsert_session(
            SessionNode(
                session_key=SESSION_KEY,
                session_id="session-2",
                created_at=_T0 + 1,
                updated_at=_T0 + 1,
            )
        )

        assert await storage.list_agent_tasks(SESSION_KEY) == []
        grouped = await storage.list_agent_tasks_for_sessions([SESSION_KEY])
        assert grouped[SESSION_KEY] == []
    finally:
        await storage.close()


async def _count(storage: SessionStorage, table: str, session_key: str) -> int:
    async with storage.conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE session_key = ?", (session_key,)
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def test_delete_session_removes_rows_from_an_earlier_session_id() -> None:
    """Child rows are session-key scoped, so a rotated session_id must not orphan them.

    ``upsert_session`` is ``ON CONFLICT(session_key) DO UPDATE SET
    session_id=excluded.session_id``, so the sessions row can carry a new
    ``session_id`` while the key stays fixed. Deleting child rows by the
    *current* ``session_id`` alone leaves everything written under the previous
    one behind -- and because session keys are deterministic, the next session
    that reuses the key inherits those rows.
    """
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        await _seed(storage, session_id="session-1", task_id="task-1")
        # Same key, rotated id — then more history under the new id.
        await _seed(storage, session_id="session-2", task_id="task-2")

        assert await _count(storage, "transcript_entries", SESSION_KEY) == 2

        await storage.delete_session(SESSION_KEY)

        assert await _count(storage, "transcript_entries", SESSION_KEY) == 0
        assert await storage.count_sessions() == 0
    finally:
        await storage.close()


async def test_delete_session_clears_every_session_scoped_table_after_rotation() -> None:
    """The same guarantee for each table delete_session touches."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        await _seed(storage, session_id="session-1", task_id="task-1")
        await _seed(storage, session_id="session-2", task_id="task-2")

        await storage.delete_session(SESSION_KEY)

        for table in (
            "transcript_entries",
            "compacted_transcript_entries",
            "session_summaries",
            "session_context_states",
            "agent_tasks",
            "memory_durable_receipts",
        ):
            assert await _count(storage, table, SESSION_KEY) == 0, f"{table} left orphans"
    finally:
        await storage.close()
