"""Tests for SessionStorage transaction serialization on shared SQLite connection (Issue #891).

Verifies that:
1. Concurrent multi-statement transactions (e.g. delete_session) do not overlap.
2. Commit-producing writers cannot interleave with an active explicit transaction.
3. Exceptions inside a transaction roll back and release transaction ownership.
4. Concurrent deletes across multiple sessions complete successfully without OperationalError.
"""

from __future__ import annotations

import asyncio

import pytest

from agentos.session.models import (
    AgentTaskRecord,
    AgentTaskStatus,
    MemoryDurableReceipt,
    ProjectNode,
    SessionNode,
    TranscriptEntry,
)
from agentos.session.storage import SessionStorage

_T0 = 1_700_000_000_000


async def _seed_session(
    storage: SessionStorage,
    session_key: str,
    session_id: str,
    task_id: str,
) -> None:
    await storage.upsert_session(
        SessionNode(
            session_key=session_key,
            session_id=session_id,
            created_at=_T0,
            updated_at=_T0,
        )
    )
    await storage.append_transcript_entry(
        TranscriptEntry(
            session_id=session_id,
            session_key=session_key,
            message_id=f"{task_id}-msg",
            role="user",
            content="test message",
            created_at=_T0,
        )
    )
    await storage.create_agent_task(
        AgentTaskRecord(
            task_id=task_id,
            session_key=session_key,
            source_kind="webui",
            queue_mode="followup",
            run_kind="web_turn",
            status=AgentTaskStatus.SUCCEEDED,
            created_at=_T0,
            updated_at=_T0,
        )
    )
    await storage.upsert_memory_durable_receipt(
        MemoryDurableReceipt(
            receipt_id=f"{task_id}-rcpt",
            session_key=session_key,
            session_id=session_id,
            turn_id="turn-1",
            scope="checkpoint",
            content_hash="hash1",
            idempotency_key=f"ckpt:{session_key}:{task_id}",
            status="checkpoint_saved",
            created_at=_T0,
            updated_at=_T0,
        )
    )


@pytest.mark.asyncio
async def test_concurrent_delete_sessions_serialized() -> None:
    """Reproduction of Issue #891: concurrent delete_session calls must not fail."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        num_sessions = 20
        keys = [f"agent:main:direct:user-{i}" for i in range(num_sessions)]

        for i, key in enumerate(keys):
            await _seed_session(
                storage,
                session_key=key,
                session_id=f"sess-{i}",
                task_id=f"task-{i}",
            )

        assert await storage.count_sessions() == num_sessions

        # Run all 20 deletes concurrently
        await asyncio.gather(*[storage.delete_session(key) for key in keys])

        # All 20 sessions and their scoped rows must be completely deleted
        assert await storage.count_sessions() == 0
        for i in range(num_sessions):
            assert await storage.get_transcript(f"sess-{i}") == []
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_transaction_blocks_interleaved_writer() -> None:
    """An ordinary writer cannot interleave and commit inside an explicit transaction."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        key = "agent:main:direct:tx-test"
        await storage.upsert_session(
            SessionNode(
                session_key=key,
                session_id="sess-orig",
                created_at=_T0,
                updated_at=_T0,
            )
        )

        in_tx_event = asyncio.Event()
        writer_completed = False
        execution_order: list[str] = []

        async def _long_transaction() -> None:
            async with storage.transaction():
                execution_order.append("tx_begin")
                in_tx_event.set()
                # Simulate work inside transaction
                await asyncio.sleep(0.05)
                await storage.conn.execute(
                    "UPDATE sessions SET display_name = 'updated_by_tx' WHERE session_key = ?",
                    (key,),
                )
                execution_order.append("tx_end")

        async def _interleaved_writer() -> None:
            await in_tx_event.wait()
            # Writer attempts to run while transaction is active
            await storage.append_transcript_entry(
                TranscriptEntry(
                    session_id="sess-orig",
                    session_key=key,
                    message_id="msg-interleaved",
                    role="user",
                    content="interleaved content",
                    created_at=_T0 + 1,
                )
            )
            nonlocal writer_completed
            writer_completed = True
            execution_order.append("writer_done")

        await asyncio.gather(_long_transaction(), _interleaved_writer())

        assert writer_completed is True
        # Transaction must have finished before the writer completed
        assert execution_order == ["tx_begin", "tx_end", "writer_done"]

        sess = await storage.get_session(key)
        assert sess is not None
        assert sess.display_name == "updated_by_tx"
        transcript = await storage.get_transcript("sess-orig")
        assert len(transcript) == 1
        assert transcript[0].content == "interleaved content"
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_transaction_rollback_releases_lock_for_subsequent_operations() -> None:
    """A failed transaction rolls back and releases the lock for later operations."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        key = "agent:main:direct:rollback-test"
        await storage.upsert_session(
            SessionNode(
                session_key=key,
                session_id="sess-rb",
                created_at=_T0,
                updated_at=_T0,
                display_name="initial",
            )
        )

        with pytest.raises(RuntimeError, match="simulated failure"):
            async with storage.transaction():
                await storage.conn.execute(
                    "UPDATE sessions SET display_name = 'dirty' WHERE session_key = ?",
                    (key,),
                )
                raise RuntimeError("simulated failure")

        # Rollback verified: display_name remains 'initial'
        sess = await storage.get_session(key)
        assert sess is not None
        assert sess.display_name == "initial"

        # Subsequent operations succeed without hanging or error
        await storage.upsert_session(
            SessionNode(
                session_key=key,
                session_id="sess-rb",
                created_at=_T0,
                updated_at=_T0 + 1,
                display_name="recovered",
            )
        )
        sess_after = await storage.get_session(key)
        assert sess_after is not None
        assert sess_after.display_name == "recovered"
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_concurrent_delete_project_and_sessions() -> None:
    """Concurrent project deletion and session deletion serialize cleanly."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        project_id = "proj-1"
        await storage.upsert_project(
            ProjectNode(
                project_id=project_id,
                name="Test Project",
                created_at=_T0,
                updated_at=_T0,
            )
        )

        num_sessions = 5
        keys = [f"agent:main:direct:proj-sess-{i}" for i in range(num_sessions)]
        for i, key in enumerate(keys):
            await storage.upsert_session(
                SessionNode(
                    session_key=key,
                    session_id=f"sess-{i}",
                    project_id=project_id,
                    created_at=_T0,
                    updated_at=_T0,
                )
            )

        # Run concurrent delete_project and delete_session calls
        tasks = [storage.delete_project(project_id)] + [storage.delete_session(key) for key in keys]
        results = await asyncio.gather(*tasks)

        assert results[0] >= 0
        assert await storage.count_sessions() == 0
        assert await storage.get_project(project_id) is None
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_nested_transaction_reentrancy_and_rollback() -> None:
    """Nested transactions and method calls within transactions execute safely without deadlocks."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        key1 = "agent:main:direct:nested-1"
        key2 = "agent:main:direct:nested-2"
        await storage.upsert_session(
            SessionNode(
                session_key=key1,
                session_id="sess-n1",
                created_at=_T0,
                updated_at=_T0,
            )
        )
        await storage.upsert_session(
            SessionNode(
                session_key=key2,
                session_id="sess-n2",
                created_at=_T0,
                updated_at=_T0,
            )
        )

        # Test 1: Re-entrant transaction call (calling delete_session inside transaction)
        async with storage.transaction():
            # delete_session also takes transaction()
            await storage.delete_session(key1)
            # writer method taking _write_context
            await storage.append_transcript_entry(
                TranscriptEntry(
                    session_id="sess-n2",
                    session_key=key2,
                    message_id="msg-nested",
                    role="user",
                    content="nested content",
                    created_at=_T0,
                )
            )

        assert await storage.get_session(key1) is None
        assert len(await storage.get_transcript("sess-n2")) == 1

        # Test 2: Nested transaction rollback rolls back both outer and inner writes
        with pytest.raises(ValueError, match="abort outer"):
            async with storage.transaction():
                await storage.upsert_session(
                    SessionNode(
                        session_key=key1,
                        session_id="sess-n1-again",
                        created_at=_T0,
                        updated_at=_T0,
                    )
                )
                async with storage.transaction():
                    await storage.append_transcript_entry(
                        TranscriptEntry(
                            session_id="sess-n1-again",
                            session_key=key1,
                            message_id="msg-aborted",
                            role="user",
                            content="aborted",
                            created_at=_T0,
                        )
                    )
                raise ValueError("abort outer")

        assert await storage.get_session(key1) is None
        assert await storage.get_transcript("sess-n1-again") == []
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_concurrent_mixed_transactions_and_writers() -> None:
    """Stress test: mixed concurrent writers, deletes, and transactions on different sessions."""
    storage = SessionStorage(":memory:")
    await storage.connect()
    try:
        num_sessions = 15
        keys = [f"agent:main:direct:stress-{i}" for i in range(num_sessions)]

        for i, key in enumerate(keys):
            await _seed_session(
                storage,
                session_key=key,
                session_id=f"stress-sess-{i}",
                task_id=f"stress-task-{i}",
            )

        async def _writer_loop(key: str, sess_id: str) -> None:
            for j in range(5):
                await storage.append_transcript_entry(
                    TranscriptEntry(
                        session_id=sess_id,
                        session_key=key,
                        message_id=f"msg-loop-{j}",
                        role="assistant",
                        content=f"msg {j}",
                        created_at=_T0 + j,
                    )
                )
                await asyncio.sleep(0.001)

        async def _deleter(key: str) -> None:
            await asyncio.sleep(0.005)
            await storage.delete_session(key)

        # Concurrently write to odd sessions and delete even sessions
        writers = [_writer_loop(keys[i], f"stress-sess-{i}") for i in range(1, num_sessions, 2)]
        deleters = [_deleter(keys[i]) for i in range(0, num_sessions, 2)]

        await asyncio.gather(*writers, *deleters)

        # Even sessions are deleted; odd sessions survived with all appended messages
        for i in range(num_sessions):
            key = keys[i]
            sess_id = f"stress-sess-{i}"
            if i % 2 == 0:
                assert await storage.get_session(key) is None
                assert await storage.get_transcript(sess_id) == []
            else:
                assert await storage.get_session(key) is not None
                transcript = await storage.get_transcript(sess_id)
                # Seed message + 5 appended messages = 6
                assert len(transcript) == 6
    finally:
        await storage.close()
