"""``tool_failure_loop_block_threshold`` must never block a call that has not run yet.

The gate blocks the Nth attempt of an identical failing call, so N-1 attempts have already
failed by the time it fires. A threshold of 1 used to block the very first attempt, before
anything could have failed, making every gated tool unusable (#3027).
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.engine import Agent, AgentConfig, ToolCall, ToolResult


class _Provider:
    """The gate is reached before the provider is, so it is never called here."""

    provider_name = "fake"

    def chat(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("the provider must not be reached")


def _agent(threshold: int, calls: list[int]) -> Agent:
    async def _failing_tool(call: ToolCall) -> ToolResult:
        calls.append(1)
        return ToolResult(
            tool_use_id=call.tool_use_id,
            tool_name=call.tool_name,
            content="write failed",
            is_error=True,
        )

    return Agent(
        provider=_Provider(),
        config=AgentConfig(tool_failure_loop_block_threshold=threshold),
        tool_handler=_failing_tool,
    )


def _call() -> ToolCall:
    return ToolCall(
        tool_use_id="write-1",
        tool_name="write_file",
        arguments={"path": "index.html", "content": "<html>bad</html>"},
    )


def _blocked(result: ToolResult) -> bool:
    status = result.execution_status or {}
    return status.get("reason") == "tool_failure_loop_exhausted"


@pytest.mark.asyncio
async def test_threshold_of_one_runs_the_first_attempt_then_blocks() -> None:
    calls: list[int] = []
    agent = _agent(1, calls)

    first = await agent._execute_tool(_call())
    second = await agent._execute_tool(_call())

    assert not _blocked(first), "the first attempt has nothing to repeat yet"
    assert len(calls) == 1
    assert _blocked(second)
    assert len(calls) == 1, "the blocked attempt must not reach the handler"


@pytest.mark.asyncio
async def test_threshold_of_one_still_allows_a_changed_call() -> None:
    calls: list[int] = []
    agent = _agent(1, calls)

    await agent._execute_tool(_call())
    other = ToolCall(
        tool_use_id="write-2",
        tool_name="write_file",
        arguments={"path": "other.html", "content": "different"},
    )
    result = await agent._execute_tool(other)

    assert not _blocked(result)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_default_threshold_still_blocks_the_third_attempt() -> None:
    calls: list[int] = []
    agent = _agent(3, calls)

    first = await agent._execute_tool(_call())
    second = await agent._execute_tool(_call())
    third = await agent._execute_tool(_call())

    assert not _blocked(first)
    assert not _blocked(second)
    assert _blocked(third)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_threshold_of_zero_never_blocks() -> None:
    calls: list[int] = []
    agent = _agent(0, calls)

    for _ in range(4):
        result = await agent._execute_tool(_call())
        assert not _blocked(result)
    assert len(calls) == 4
