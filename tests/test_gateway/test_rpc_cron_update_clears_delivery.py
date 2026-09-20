"""``cron.update`` with ``delivery: null`` removes the job's delivery route.

Leaving ``delivery`` out of the request already means "unchanged", so an explicit null can
only be asking for removal. Every branch of the delivery patch needed a dict, so null fell
through all of them: the call reported success and the old channel route stayed live (#3030).

The handlers run against a real ``SchedulerEngine`` on an in-memory ``JobStore`` and the job
is read back from the store, so what is asserted is what was persisted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_cron import _handle_cron_add, _handle_cron_update
from agentos.scheduler.engine import SchedulerEngine
from agentos.scheduler.persistence import JobStore
from agentos.scheduler.types import CronJob, DeliveryMode

CHANNEL_DELIVERY = {"mode": "channel", "channelName": "slack", "channelId": "C123"}


@pytest.fixture
async def ctx() -> AsyncIterator[RpcContext]:
    async with JobStore(":memory:") as store:
        yield RpcContext(conn_id="test", cron_scheduler=SchedulerEngine(store))


async def _add(ctx: RpcContext, **params: Any) -> str:
    job = await _handle_cron_add(
        {
            "name": "alert",
            "expression": "0 0 * * *",
            "message": "ping",
            "sessionTarget": "isolated",
            **params,
        },
        ctx,
    )
    return str(job["id"])


async def _stored(ctx: RpcContext, job_id: str) -> CronJob:
    job = await ctx.cron_scheduler.get_job(job_id)  # type: ignore[union-attr]
    assert job is not None
    return job


@pytest.mark.asyncio
async def test_explicit_null_delivery_clears_the_channel_route(ctx: RpcContext) -> None:
    job_id = await _add(ctx, delivery=CHANNEL_DELIVERY)
    assert (await _stored(ctx, job_id)).delivery.mode == DeliveryMode.CHANNEL

    await _handle_cron_update({"id": job_id, "delivery": None}, ctx)

    delivery = (await _stored(ctx, job_id)).delivery
    assert delivery.mode != DeliveryMode.CHANNEL
    assert delivery.channel_name == ""
    assert delivery.channel_id == ""


@pytest.mark.asyncio
async def test_explicit_null_delivery_matches_mode_none(ctx: RpcContext) -> None:
    """``{"mode": "none"}`` is the documented way to clear; null must land in the same place."""
    null_job = await _add(ctx, delivery=CHANNEL_DELIVERY)
    none_job = await _add(ctx, delivery=CHANNEL_DELIVERY)

    await _handle_cron_update({"id": null_job, "delivery": None}, ctx)
    await _handle_cron_update({"id": none_job, "delivery": {"mode": "none"}}, ctx)

    cleared = (await _stored(ctx, null_job)).delivery
    reference = (await _stored(ctx, none_job)).delivery
    assert cleared.mode == reference.mode
    assert cleared.channel_name == reference.channel_name
    assert cleared.channel_id == reference.channel_id


@pytest.mark.asyncio
async def test_omitting_delivery_still_leaves_it_untouched(ctx: RpcContext) -> None:
    job_id = await _add(ctx, delivery=CHANNEL_DELIVERY)

    await _handle_cron_update({"id": job_id, "name": "renamed"}, ctx)

    job = await _stored(ctx, job_id)
    assert job.name == "renamed"
    assert job.delivery.mode == DeliveryMode.CHANNEL
    assert job.delivery.channel_id == "C123"
