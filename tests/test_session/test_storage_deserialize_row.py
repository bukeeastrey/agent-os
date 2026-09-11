"""Tests for _deserialize_row and JSON deserialization error logging."""

from __future__ import annotations

import logging
import sqlite3

import pytest

from agentos.session.storage import SessionStorage, _deserialize_row


def test_deserialize_row_valid_json():
    row = {
        "session_key": "agent:test:session-1",
        "tool_calls": '[{"name": "test_tool", "arguments": {}}]',
        "total_tokens_fresh": 1,
        "raw_text": "hello",
    }
    deserialized = _deserialize_row(row)
    assert deserialized["tool_calls"] == [{"name": "test_tool", "arguments": {}}]
    assert deserialized["total_tokens_fresh"] is True
    assert deserialized["raw_text"] == "hello"


def test_deserialize_row_corrupted_json_logs_warning_with_session_key(caplog):
    row = {
        "session_key": "agent:test:session-corrupted",
        "tool_calls": '{"name": "truncated_json',
    }
    with caplog.at_level(logging.WARNING, logger="agentos.session.storage"):
        deserialized = _deserialize_row(row)

    assert deserialized["tool_calls"] is None
    assert deserialized["session_key"] == "agent:test:session-corrupted"
    assert any(
        "Failed to deserialize JSON field 'tool_calls' for row 'agent:test:session-corrupted'"
        in record.message
        for record in caplog.records
    )


def test_deserialize_row_corrupted_json_fallback_identifiers(caplog):
    identifiers = [
        ({"session_id": "sess-xyz"}, "sess-xyz"),
        ({"message_id": "msg-123"}, "msg-123"),
        ({"task_id": "task-abc"}, "task-abc"),
        ({"receipt_id": "rcpt-789"}, "rcpt-789"),
        ({"summary_id": "summ-456"}, "summ-456"),
        ({"state_key": "state-k"}, "state-k"),
        ({"id": "pk-999"}, "pk-999"),
        ({}, "<unknown>"),
    ]

    for row_id_dict, expected_ident in identifiers:
        caplog.clear()
        row = {
            **row_id_dict,
            "payload": "{not valid json",
        }
        with caplog.at_level(logging.WARNING, logger="agentos.session.storage"):
            deserialized = _deserialize_row(row)

        assert deserialized["payload"] is None
        assert any(
            f"Failed to deserialize JSON field 'payload' for row '{expected_ident}'"
            in record.message
            for record in caplog.records
        )


@pytest.mark.asyncio
async def test_session_storage_reads_corrupted_transcript_entry_with_warning(tmp_path, caplog):
    db_path = tmp_path / "test-storage.db"
    storage = await SessionStorage.open(str(db_path))

    session_id = "sess-id-1"
    session_key = "agent:test:sess-int"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO transcript_entries (
                session_id, session_key, message_id, role, content, tool_calls, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                session_key,
                "msg-corrupted",
                "assistant",
                "corrupted entry",
                "{malformed json",
                1000,
            ),
        )

    with caplog.at_level(logging.WARNING, logger="agentos.session.storage"):
        entries = await storage.get_transcript(session_id)

    await storage.close()

    assert len(entries) == 1
    assert entries[0].tool_calls is None
    expected_msg = f"Failed to deserialize JSON field 'tool_calls' for row '{session_key}'"
    assert any(expected_msg in record.message for record in caplog.records)
