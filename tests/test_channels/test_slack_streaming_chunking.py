"""``SlackChannel.send_streaming`` had no message-length chunking.

``_post`` / ``_edit`` sent the whole ``StreamThrottle``-accumulated text to
``chat.postMessage`` / ``chat.update`` with no check against
``_SLACK_MESSAGE_TEXT_LIMIT``. Slack truncates (and may split) a ``text`` field
past 40000 characters, so the tail of a long streamed reply was lost -- and since
the ``ok`` verification landed (#2526), an over-limit payload comes back
``msg_too_long`` and raises instead, killing the whole reply.

``SlackChannel.send`` has chunked since #1544, and both
``TelegramChannel.send_streaming`` and ``DiscordChannel.send_streaming`` already
roll a long stream over into a new message; Slack's streaming path was the gap.

Rolling over mid-stream is safe here for the same reason it is on Discord: the
chunks ``send_streaming`` receives are append-only deltas, so text already sent
is never revised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentos.channels.slack import _SLACK_MESSAGE_TEXT_LIMIT, SlackChannel


class _SlackResponse:
    status_code = 200

    def __init__(self, ts: str) -> None:
        self._ts = ts

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"ok": True, "ts": self._ts}


def _streaming_channel() -> tuple[SlackChannel, list[tuple[str, dict[str, Any]]]]:
    """A channel whose client records (url, payload) for every API call.

    ``chat.postMessage`` mints a fresh ts, as Slack does; ``chat.update`` echoes
    back the ts it was given.
    """
    channel = SlackChannel(token="xoxb-test", slack_channel_id="C1")
    client = AsyncMock()
    calls: list[tuple[str, dict[str, Any]]] = []
    created = 0

    async def _post(url: str, **kwargs: Any) -> _SlackResponse:
        nonlocal created
        payload = kwargs.get("json", {})
        calls.append((url, payload))
        if url.endswith("/chat.update"):
            return _SlackResponse(str(payload.get("ts")))
        created += 1
        return _SlackResponse(f"ts-{created}")

    client.post = _post
    channel._client = client
    return channel, calls


async def _one_chunk(text: str) -> AsyncIterator[str]:
    yield text


def _posts(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """The payloads that opened a new message (chat.postMessage)."""
    return [payload for url, payload in calls if url.endswith("/chat.postMessage")]


@pytest.mark.asyncio
async def test_a_long_reply_rolls_into_several_messages() -> None:
    channel, calls = _streaming_channel()
    long_content = "x" * (_SLACK_MESSAGE_TEXT_LIMIT * 2 + 500)

    result = await channel.send_streaming(_one_chunk(long_content), channel="C1")

    posts = _posts(calls)
    assert len(posts) == 3
    assert "".join(str(payload["text"]) for payload in posts) == long_content, (
        "every character must be delivered, in order, across the messages"
    )
    assert result == "ts-3", "the ts returned is the last message written"


@pytest.mark.asyncio
async def test_no_api_call_exceeds_the_cap() -> None:
    """Holds for chat.update too: an edit that outgrew the cap is what failed."""
    channel, calls = _streaming_channel()

    await channel.send_streaming(_one_chunk("y" * 90_000), channel="C1")

    assert calls, "the reply must be sent"
    assert max(len(payload["text"]) for _url, payload in calls) <= _SLACK_MESSAGE_TEXT_LIMIT


@pytest.mark.asyncio
async def test_a_short_reply_is_still_one_call() -> None:
    channel, calls = _streaming_channel()

    result = await channel.send_streaming(_one_chunk("hello"), channel="C1")

    assert len(_posts(calls)) == 1, "a short reply opens exactly one message"
    assert [payload["text"] for _url, payload in calls] == ["hello", "hello"]
    assert result == "ts-1"


@pytest.mark.parametrize(
    ("length", "expected_calls"),
    [(_SLACK_MESSAGE_TEXT_LIMIT, 1), (_SLACK_MESSAGE_TEXT_LIMIT + 1, 2)],
)
@pytest.mark.asyncio
async def test_boundary_at_exactly_the_cap(length: int, expected_calls: int) -> None:
    channel, calls = _streaming_channel()

    await channel.send_streaming(_one_chunk("z" * length), channel="C1")

    assert len(_posts(calls)) == expected_calls


@pytest.mark.asyncio
async def test_a_rolled_over_reply_stays_in_its_thread() -> None:
    channel, calls = _streaming_channel()

    await channel.send_streaming(
        _one_chunk("t" * (_SLACK_MESSAGE_TEXT_LIMIT + 10)), channel="C1", thread_ts="1700.1"
    )

    posts = _posts(calls)
    assert len(posts) == 2
    for payload in posts:
        assert payload["thread_ts"] == "1700.1", "the rollover message joins the same thread"
