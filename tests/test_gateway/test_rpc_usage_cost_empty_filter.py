"""``usage.cost`` -- a filter that matches no record is an empty ledger, not an error.

When a tool, skill or date filter matched nothing, the handler raised ValueError on the
way to its session-level fallback, so ``agentos cost --tool <name>`` for a tool that has
not run yet failed the RPC instead of reporting zero (#3036).

The decline still stands where it was meant to: with no cost ledger at all, the question
cannot be answered, and session totals carry no tool name, skill or per-record timestamp
to filter by.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentos.engine.usage import UsageTracker
from agentos.gateway.rpc_usage import _handle_usage_cost


class _Sessions:
    async def list_sessions(self) -> list[Any]:
        return []


def _ctx(tracker: UsageTracker | None) -> Any:
    class _Ctx:
        usage_tracker = tracker
        session_manager = _Sessions()

    return _Ctx()


@pytest.fixture
def tracker(tmp_path: Path) -> UsageTracker:
    tracker = UsageTracker(default_provider_id="openrouter", db_path=str(tmp_path / "usage.db"))
    tracker.add(
        session_key="agent:agent-1:telegram:session-1",
        model_id="anthropic/claude-3-haiku",
        input_tokens=100,
        output_tokens=50,
        billed_cost=0.0005,
    )
    return tracker


@pytest.mark.asyncio
async def test_a_tool_filter_matching_nothing_returns_an_empty_ledger(
    tracker: UsageTracker,
) -> None:
    result = await _handle_usage_cost({"tool": "non_existent_tool"}, _ctx(tracker))

    assert result == {"breakdown": [], "totalCostUsd": 0.0}


@pytest.mark.asyncio
async def test_a_skill_filter_matching_nothing_returns_an_empty_ledger(
    tracker: UsageTracker,
) -> None:
    result = await _handle_usage_cost({"skill": "never-used"}, _ctx(tracker))

    assert result == {"breakdown": [], "totalCostUsd": 0.0}


@pytest.mark.asyncio
async def test_a_date_range_matching_nothing_returns_an_empty_ledger(
    tracker: UsageTracker,
) -> None:
    result = await _handle_usage_cost(
        {"startDate": "1999-01-01", "endDate": "1999-01-02"}, _ctx(tracker)
    )

    assert result == {"breakdown": [], "totalCostUsd": 0.0}


@pytest.mark.asyncio
async def test_a_matching_filter_still_returns_its_records(tracker: UsageTracker) -> None:
    result = await _handle_usage_cost({"startDate": "2020-01-01"}, _ctx(tracker))

    assert len(result["breakdown"]) == 1
    assert result["breakdown"][0]["agentId"] == "agent-1"


@pytest.mark.asyncio
async def test_without_a_ledger_the_filtered_query_still_declines() -> None:
    """Session totals cannot filter by tool, skill or date, so the decline stays."""
    with pytest.raises(ValueError, match="The cost ledger returned no records"):
        await _handle_usage_cost({"toolName": "custom_tool"}, _ctx(None))
