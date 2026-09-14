"""``StreamThrottle`` contract coverage at the utility boundary.

The adapter suites exercise streaming through a channel; these tests pin
the throttle's own contract — that the throttle window is honoured even
when several producers flush concurrently, and that a serialized waiter
does not issue a redundant round trip.
"""

from __future__ import annotations

import asyncio

import pytest

from agentos.channels._util import StreamThrottle


class _Recorder:
    """Records post/edit calls and yields control so flushes can interleave."""

    def __init__(self) -> None:
        self.posts: list[str] = []
        self.edits: list[str] = []

    async def post(self, text: str) -> str:
        self.posts.append(text)
        await asyncio.sleep(0)
        return "posted"

    async def edit(self, text: str) -> str:
        self.edits.append(text)
        await asyncio.sleep(0)
        return "edited"


@pytest.mark.asyncio
async def test_concurrent_flushes_send_once() -> None:
    """Two concurrent flushes must produce one round trip, not two.

    The throttle window is re-checked under the lock, so the caller that
    queues behind the in-flight post finds the snapshot already sent.
    """
    throttle = StreamThrottle(interval_s=5.0)
    rec = _Recorder()
    throttle.add("a")

    await asyncio.gather(
        throttle.maybe_flush(post=rec.post, edit=rec.edit),
        throttle.maybe_flush(post=rec.post, edit=rec.edit),
    )

    assert rec.posts == ["a"]
    assert rec.edits == [], "a queued waiter must not re-send the same snapshot"


@pytest.mark.asyncio
async def test_many_concurrent_flushes_send_once() -> None:
    """The guarantee holds for more than two racers."""
    throttle = StreamThrottle(interval_s=5.0)
    rec = _Recorder()
    throttle.add("chunk")

    await asyncio.gather(*(throttle.maybe_flush(post=rec.post, edit=rec.edit) for _ in range(8)))

    assert len(rec.posts) + len(rec.edits) == 1


@pytest.mark.asyncio
async def test_queued_waiter_returns_none() -> None:
    """The suppressed caller reports that it sent nothing."""
    throttle = StreamThrottle(interval_s=5.0)
    rec = _Recorder()
    throttle.add("a")

    results = await asyncio.gather(
        throttle.maybe_flush(post=rec.post, edit=rec.edit),
        throttle.maybe_flush(post=rec.post, edit=rec.edit),
    )

    assert sorted(r is None for r in results) == [False, True]


@pytest.mark.asyncio
async def test_edit_sent_once_window_has_elapsed() -> None:
    """Throttling suppresses redundant sends; it does not suppress real ones."""
    throttle = StreamThrottle(interval_s=0.0)
    rec = _Recorder()

    throttle.add("a")
    await throttle.maybe_flush(post=rec.post, edit=rec.edit)
    throttle.add("b")
    await throttle.maybe_flush(post=rec.post, edit=rec.edit)

    assert rec.posts == ["a"]
    assert rec.edits == ["ab"]


@pytest.mark.asyncio
async def test_force_flush_still_bypasses_the_window() -> None:
    """End-of-stream delivery must not be throttled away."""
    throttle = StreamThrottle(interval_s=99.0)
    rec = _Recorder()

    throttle.add("a")
    await throttle.maybe_flush(post=rec.post, edit=rec.edit)
    throttle.add("b")
    await throttle.force_flush(post=rec.post, edit=rec.edit)

    assert rec.posts == ["a"]
    assert rec.edits == ["ab"]


@pytest.mark.asyncio
async def test_empty_throttle_sends_nothing() -> None:
    throttle = StreamThrottle(interval_s=0.0)
    rec = _Recorder()

    assert await throttle.maybe_flush(post=rec.post, edit=rec.edit) is None
    assert rec.posts == [] and rec.edits == []


@pytest.mark.asyncio
async def test_failed_send_keeps_the_snapshot_for_retry() -> None:
    """A raising send must not consume the accumulated text."""
    throttle = StreamThrottle(interval_s=0.0)

    async def boom(_text: str) -> None:
        raise RuntimeError("network down")

    throttle.add("a")
    with pytest.raises(RuntimeError):
        await throttle.maybe_flush(post=boom, edit=boom)

    assert throttle.text == "a"
    assert not throttle.opened
