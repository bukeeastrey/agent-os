"""``cron.add`` / ``cron.update`` must reject a payloadKind this server does not have.

``_build_payload`` handled ``script``, ``system_event`` and ``reminder`` explicitly and let
everything else fall through to the agent_turn branch. A typo therefore created an LLM turn
job -- the most expensive kind there is -- and the call reported success (#3029).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_cron import _handle_cron_add, _handle_cron_update
from agentos.scheduler.engine import SchedulerEngine
from agentos.scheduler.persistence import JobStore


@pytest.fixture
async def ctx() -> AsyncIterator[RpcContext]:
    async with JobStore(":memory:") as store:
        yield RpcContext(conn_id="test", cron_scheduler=SchedulerEngine(store))


def _params(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "backup",
        "expression": "0 * * * *",
        "message": "check system",
        "sessionTarget": "isolated",
        **overrides,
    }


@pytest.mark.asyncio
async def test_cron_add_rejects_an_unknown_payload_kind(ctx: RpcContext) -> None:
    with pytest.raises(ValueError) as excinfo:
        await _handle_cron_add(_params(payloadKind="unknown_kind"), ctx)

    message = str(excinfo.value)
    assert "unknown_kind" in message
    assert "agent_turn" in message, "the error should name the kinds that do exist"
    assert await ctx.cron_scheduler.list_jobs() == [], "nothing may be created"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cron_update_rejects_an_unknown_payload_kind(ctx: RpcContext) -> None:
    job = await _handle_cron_add(_params(payloadKind="reminder"), ctx)

    with pytest.raises(ValueError):
        await _handle_cron_update({"id": job["id"], "payloadKind": "unknown_kind"}, ctx)

    stored = await ctx.cron_scheduler.get_job(job["id"])  # type: ignore[union-attr]
    assert stored is not None
    assert stored.payload.get("kind") == "reminder", "the job must be left as it was"


@pytest.mark.parametrize("kind", ["agent_turn", "reminder"])
@pytest.mark.asyncio
async def test_known_payload_kinds_are_still_accepted(ctx: RpcContext, kind: str) -> None:
    job = await _handle_cron_add(_params(payloadKind=kind), ctx)

    assert job["payloadKind"] == kind


@pytest.mark.asyncio
async def test_system_event_is_still_accepted_on_main(ctx: RpcContext) -> None:
    job = await _handle_cron_add(
        _params(payloadKind="system_event", sessionTarget="main", sessionKey="agent:main:main"),
        ctx,
    )

    assert job["payloadKind"] == "system_event"


@pytest.mark.asyncio
async def test_an_omitted_payload_kind_still_defaults(ctx: RpcContext) -> None:
    job = await _handle_cron_add(_params(), ctx)

    assert job["payloadKind"] == "reminder"
