from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agentos.channels.contract import ChannelCapabilities, ChannelSendStatus
from agentos.channels.stream_policy import resolve_channel_stream_policy
from agentos.channels.telegram import TelegramApiError, TelegramChannel, TelegramChannelConfig
from agentos.channels.types import IncomingMessage
from agentos.gateway import channel_dispatch


def _install_blocking_keepalive_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[float], asyncio.Event]:
    sleep_intervals: list[float] = []
    sleep_started = asyncio.Event()
    block_sleep = asyncio.Event()

    async def fake_sleep(interval: float) -> None:
        sleep_intervals.append(interval)
        sleep_started.set()
        await block_sleep.wait()

    monkeypatch.setattr(
        channel_dispatch,
        "asyncio",
        SimpleNamespace(create_task=asyncio.create_task, sleep=fake_sleep),
    )
    return sleep_intervals, sleep_started


@pytest.mark.asyncio
async def test_telegram_send_typing_posts_native_chat_action_for_topic() -> None:
    channel = TelegramChannel(TelegramChannelConfig(token="token"))
    calls: list[tuple[str, dict[str, Any] | None]] = []

    async def fake_api(method: str, payload: dict[str, Any] | None = None) -> bool:
        calls.append((method, payload))
        return True

    channel._api = fake_api  # type: ignore[method-assign]  # noqa: SLF001

    result = await channel.send_typing(channel_id="-100123", thread_id="777")

    assert calls == [
        (
            "sendChatAction",
            {
                "chat_id": "-100123",
                "action": "typing",
                "message_thread_id": 777,
            },
        )
    ]
    assert result.status == ChannelSendStatus.SENT
    assert result.capability == ChannelCapabilities.TYPING_INDICATOR
    assert result.target_id == "-100123"


@pytest.mark.asyncio
async def test_telegram_send_typing_uses_default_target() -> None:
    channel = TelegramChannel(TelegramChannelConfig(token="token", default_chat_id="default-chat"))
    calls: list[tuple[str, dict[str, Any] | None]] = []

    async def fake_api(method: str, payload: dict[str, Any] | None = None) -> bool:
        calls.append((method, payload))
        return True

    channel._api = fake_api  # type: ignore[method-assign]  # noqa: SLF001

    result = await channel.send_typing()

    assert calls == [("sendChatAction", {"chat_id": "default-chat", "action": "typing"})]
    assert result.status == ChannelSendStatus.SENT
    assert result.target_id == "default-chat"


@pytest.mark.asyncio
async def test_telegram_send_typing_without_target_is_unsupported() -> None:
    channel = TelegramChannel(TelegramChannelConfig(token="token"))

    async def unexpected_api(_method: str, _payload: dict[str, Any] | None = None) -> bool:
        raise AssertionError("sendChatAction must not run without a target")

    channel._api = unexpected_api  # type: ignore[method-assign]  # noqa: SLF001

    result = await channel.send_typing()

    assert result.status == ChannelSendStatus.UNSUPPORTED
    assert result.capability == ChannelCapabilities.TYPING_INDICATOR
    assert result.reason == "no chat target"


def test_telegram_typing_capability_selects_keepalive_policy() -> None:
    channel = TelegramChannel(TelegramChannelConfig())

    policy = resolve_channel_stream_policy(channel)

    assert channel.capability_profile.typing_indicator is True
    assert ChannelCapabilities.TYPING_INDICATOR in channel.capabilities
    assert policy.mode == "adapter_stream"
    assert policy.relay_stream is True
    assert policy.typing_keepalive is False
    assert 0 < channel.typing_keepalive_interval_s < 5


@pytest.mark.asyncio
async def test_telegram_keepalive_uses_inbound_chat_topic_and_adapter_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = TelegramChannel(TelegramChannelConfig(token="token"))
    api_calls: list[tuple[str, dict[str, Any] | None]] = []

    async def fake_api(method: str, payload: dict[str, Any] | None = None) -> bool:
        api_calls.append((method, payload))
        return True

    channel._api = fake_api  # type: ignore[method-assign]  # noqa: SLF001
    sleep_intervals, sleep_started = _install_blocking_keepalive_sleep(monkeypatch)
    inbound = IncomingMessage(
        sender_id="user-1",
        channel_id="-100123",
        content="hello",
        metadata={"is_group": True, "thread_id": "777"},
    )

    task = channel_dispatch._start_typing_keepalive(channel, inbound)

    assert task is None
    assert api_calls == []
    assert sleep_intervals == []
    assert sleep_started.is_set() is False

from collections.abc import AsyncIterator
@pytest.mark.asyncio
async def test_telegram_keepalive_treats_api_failure_as_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = TelegramChannel(TelegramChannelConfig(token="token"))
    attempts = 0

    async def failing_api(_method: str, _payload: dict[str, Any] | None = None) -> bool:
        nonlocal attempts
        attempts += 1
        raise TelegramApiError("rate limited")

    channel._api = failing_api  # type: ignore[method-assign]  # noqa: SLF001
    sleep_intervals, sleep_started = _install_blocking_keepalive_sleep(monkeypatch)
    inbound = IncomingMessage(
        sender_id="user-1",
        channel_id="chat-1",
        content="hello",
    )

    task = channel_dispatch._start_typing_keepalive(channel, inbound)

    assert task is None
    assert attempts == 0
    assert sleep_intervals == []
    assert sleep_started.is_set() is False

@pytest.mark.asyncio
async def test_send_streaming_posts_then_edits() -> None:
    calls: list[tuple[str, dict]] = []

    async def fake_api(method: str, payload: dict) -> dict:
        calls.append((method, payload))

        if method == "sendMessage":
            return {"message_id": 42}

        return {"ok": True}

    channel = TelegramChannel(
        TelegramChannelConfig(
            token="test-token",
            default_chat_id="-100111",
        )
    )

    channel._api = fake_api  # type: ignore[method-assign]

    async def chunks() -> AsyncIterator[str]:
        yield "Hello "
        yield "world"

    result = await channel.send_streaming(
        chunks(),
        channel_id="-100111",
        thread_id="5",
    )

    assert result.provider_message_id == "-100111|42"

    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["chat_id"] == "-100111"
    assert calls[0][1]["message_thread_id"] == 5

    assert calls[-1][0] == "editMessageText"
    assert calls[-1][1]["chat_id"] == "-100111"
    assert calls[-1][1]["message_id"] == 42

def test_telegram_streaming_reply_kwargs_preserve_thread() -> None:
    inbound = IncomingMessage(
        content="hello",
        channel_id="-100777",
        metadata={"thread_id": "5"},
    )

    channel = TelegramChannel(
        TelegramChannelConfig(token="test-token")
    )

    assert channel.streaming_reply_kwargs(inbound) == {
        "channel_id": "-100777",
        "thread_id": "5",
    }
