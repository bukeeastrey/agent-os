"""sessionTarget=main must resolve a session key, not crash the handler.

``_resolve_session_key`` raised NotImplementedError for MAIN long after the
heartbeat mechanism it was waiting on landed. The rest of the module already
resolves MAIN: ``_build_cron_tool_context`` falls back to
``build_main_key(agent_id)`` and ``make_system_event_handler`` uses
``job.session_key or build_main_key(agent_id)``.

``normalize_contract`` rejects agent_turn/reminder/script with
sessionTarget="main" on create, so a *new* job cannot reach the raise. The
persistence loaders call it with ``strict=False``, though, so a row written
before that validation existed still loads with ``handler_key="agent_run"``
and target MAIN -- and is then dispatched to the handler that crashes.
"""

from __future__ import annotations

import pytest

from agentos.scheduler.handlers import _resolve_session_key
from agentos.scheduler.payloads import normalize_contract
from agentos.scheduler.types import CronJob, SessionTarget
from agentos.session.keys import build_main_key

AGENT_TURN_PAYLOAD = {"kind": "agent_turn", "agent_id": "main", "task": "Check status"}


def _legacy_main_job(**overrides: object) -> CronJob:
    """A job shaped like a row that predates the create-time MAIN validation."""
    handler_key, payload, target, _ = normalize_contract(
        handler_key="agent_run",
        payload=AGENT_TURN_PAYLOAD,
        session_target=SessionTarget.MAIN,
        strict=False,
    )
    fields: dict = {
        "id": "legacy-job",
        "name": "Legacy main job",
        "cron_expr": "0 * * * *",
        "handler_key": handler_key,
        "session_target": target,
        "payload": payload,
    }
    fields.update(overrides)
    return CronJob(**fields)


def test_create_path_still_rejects_main_for_agent_turn() -> None:
    """The guard that keeps new jobs out of this state must stay."""
    with pytest.raises(ValueError, match="cannot use sessionTarget='main'"):
        normalize_contract(
            handler_key="agent_run",
            payload=AGENT_TURN_PAYLOAD,
            session_target=SessionTarget.MAIN,
            strict=True,
        )


def test_legacy_main_row_still_loads_through_the_non_strict_path() -> None:
    """Reachability: strict=False is how such a row survives a restart."""
    job = _legacy_main_job()
    assert job.handler_key == "agent_run"
    assert job.session_target == SessionTarget.MAIN


def test_main_target_resolves_to_the_main_agent_key() -> None:
    assert _resolve_session_key(_legacy_main_job()) == build_main_key("main")


def test_main_target_prefers_a_bound_session_key() -> None:
    """Matches make_system_event_handler: `job.session_key or build_main_key(...)`."""
    job = _legacy_main_job(session_key="agent:main:bound")
    assert _resolve_session_key(job) == "agent:main:bound"


def test_main_target_resolution_matches_the_system_event_handler() -> None:
    """Both resolvers must agree, or one job would run under two keys."""
    from agentos.scheduler.payloads import payload_agent_id

    job = _legacy_main_job()
    expected = job.session_key or build_main_key(payload_agent_id(job.payload))
    assert _resolve_session_key(job) == expected


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(SessionTarget.ISOLATED, id="isolated"),
        pytest.param(SessionTarget.SESSION, id="session"),
    ],
)
def test_other_targets_are_unchanged(target: SessionTarget) -> None:
    job = _legacy_main_job(session_target=target)
    resolved = _resolve_session_key(job)
    assert resolved.startswith("cron:legacy-job")


def test_current_target_without_binding_still_raises() -> None:
    """CURRENT keeps its own explicit error; only MAIN changed."""
    job = _legacy_main_job(session_target=SessionTarget.CURRENT)
    with pytest.raises(ValueError, match="CURRENT target requires a bound session key"):
        _resolve_session_key(job)
