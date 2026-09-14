"""sessions.patch must tell subscribers the row changed.

Only the project-move branch broadcast ``sessions.changed``. A patch that
renamed a session, or changed its model, thinking level or metadata, was
persisted silently -- every connected client kept showing the old value
until something unrelated happened to refresh it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from agentos.gateway import rpc_sessions
from agentos.gateway.config import GatewayConfig
from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.session.manager import SessionManager
from agentos.session.storage import SessionStorage

KEY = "agent:main:webchat:22220001"


@pytest.fixture
def dispatcher():
    return get_dispatcher()


@pytest_asyncio.fixture
async def manager():
    storage = SessionStorage(":memory:")
    await storage.connect()
    mgr = SessionManager(storage, inject_time_prefix=False)
    yield mgr
    await storage.close()


@pytest.fixture
def ctx(manager) -> RpcContext:
    context = RpcContext(conn_id="test-conn", config=GatewayConfig())
    context.session_manager = manager
    return context


@pytest.fixture
def emitted(monkeypatch) -> list[tuple[str, str, dict]]:
    """Capture _emit_to_subscribers calls.

    The real helper returns early when ctx has no subscription_manager, which
    is why a broadcast regression is invisible to the existing suite.
    """
    recorded: list[tuple[str, str, dict]] = []

    async def _record(_ctx, session_key, event_name, payload):
        recorded.append((session_key, event_name, payload))

    monkeypatch.setattr(rpc_sessions, "_emit_to_subscribers", _record)
    return recorded


def _changed(emitted: list[tuple[str, str, dict]]) -> list[dict]:
    return [payload for _key, name, payload in emitted if name == "sessions.changed"]


@pytest.mark.asyncio
async def test_display_name_patch_broadcasts(dispatcher, ctx, manager, emitted) -> None:
    await manager.create(KEY, agent_id="main")

    res = await dispatcher.dispatch(
        "r1", "sessions.patch", {"key": KEY, "displayName": "My Renamed Session"}, ctx
    )

    assert res.ok is True
    payloads = _changed(emitted)
    assert payloads, "a displayName patch must broadcast sessions.changed"
    assert payloads[0]["reason"] == "session_patched"
    assert payloads[0]["key"] == KEY
    assert "displayName" in payloads[0]["updated"]


@pytest.mark.asyncio
async def test_broadcast_carries_the_new_display_name(dispatcher, ctx, manager, emitted) -> None:
    """Clients can refresh a list row without a follow-up round trip."""
    await manager.create(KEY, agent_id="main")

    await dispatcher.dispatch("r1", "sessions.patch", {"key": KEY, "displayName": "Renamed"}, ctx)

    assert _changed(emitted)[0]["display_name"] == "Renamed"


@pytest.mark.asyncio
async def test_model_patch_broadcasts(dispatcher, ctx, manager, emitted) -> None:
    await manager.create(KEY, agent_id="main")

    res = await dispatcher.dispatch(
        "r1", "sessions.patch", {"key": KEY, "model": "claude-opus-5"}, ctx
    )

    assert res.ok is True
    payloads = _changed(emitted)
    assert payloads and payloads[0]["reason"] == "session_patched"
    assert "model" in payloads[0]["updated"]


@pytest.mark.asyncio
async def test_thinking_level_patch_broadcasts(dispatcher, ctx, manager, emitted) -> None:
    await manager.create(KEY, agent_id="main")

    res = await dispatcher.dispatch(
        "r1", "sessions.patch", {"key": KEY, "thinkingLevel": "high"}, ctx
    )

    assert res.ok is True
    assert "thinkingLevel" in _changed(emitted)[0]["updated"]


@pytest.mark.asyncio
async def test_metadata_patch_is_currently_a_no_op(dispatcher, ctx, manager, emitted) -> None:
    """Documents existing behaviour, unchanged by this PR.

    ``field_map`` maps ``metadata`` to the attribute ``meta``, but SessionNode
    has no such attribute, so the ``hasattr`` guard skips it and nothing is
    written. No update means nothing to announce, so no broadcast is correct
    here -- but it does mean a metadata patch reports success while storing
    nothing. Worth its own issue rather than papering over it from here.
    """
    await manager.create(KEY, agent_id="main")

    res = await dispatcher.dispatch(
        "r1", "sessions.patch", {"key": KEY, "metadata": {"pinned": True}}, ctx
    )

    assert res.ok is True
    assert res.payload["updated"] == []
    assert _changed(emitted) == []


@pytest.mark.asyncio
async def test_no_recognized_field_broadcasts_nothing(dispatcher, ctx, manager, emitted) -> None:
    """A no-op patch must stay silent rather than churn every client."""
    await manager.create(KEY, agent_id="main")

    res = await dispatcher.dispatch("r1", "sessions.patch", {"key": KEY, "bogus": 1}, ctx)

    assert res.ok is True
    assert _changed(emitted) == []


@pytest.mark.asyncio
async def test_project_move_still_reports_project_moved(dispatcher, ctx, manager, emitted) -> None:
    """The existing reason code must not be replaced."""
    project = await manager.create_project("proj", "Proj")
    await manager.create(KEY, agent_id="main")

    res = await dispatcher.dispatch(
        "r1", "sessions.patch", {"key": KEY, "projectId": project["project_id"]}, ctx
    )

    assert res.ok is True
    reasons = [payload["reason"] for payload in _changed(emitted)]
    assert "project_moved" in reasons


@pytest.mark.asyncio
async def test_combined_patch_reports_both_reasons(dispatcher, ctx, manager, emitted) -> None:
    project = await manager.create_project("proj2", "Proj2")
    await manager.create(KEY, agent_id="main")

    res = await dispatcher.dispatch(
        "r1",
        "sessions.patch",
        {"key": KEY, "displayName": "Both", "projectId": project["project_id"]},
        ctx,
    )

    assert res.ok is True
    reasons = [payload["reason"] for payload in _changed(emitted)]
    assert "session_patched" in reasons
    assert "project_moved" in reasons
