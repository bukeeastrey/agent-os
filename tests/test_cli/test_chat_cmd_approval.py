from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from agentos.cli.repl import approval as approval_mod


class _FakeLive:
    def __init__(self) -> None:
        self.stopped = False
        self.started = False

    def stop(self) -> None:
        self.stopped = True

    def start(self) -> None:
        self.started = True


@pytest.mark.asyncio
async def test_maybe_handle_approval_pending_prompts_and_resolves(monkeypatch) -> None:
    live = _FakeLive()
    buffer = StringIO()
    monkeypatch.setattr(
        approval_mod,
        "console",
        Console(file=buffer, force_terminal=False, width=100, highlight=False),
    )
    calls: list[tuple[str, bool, bool]] = []

    async def _prompt(_: str, **_kwargs) -> str:
        return "o"

    monkeypatch.setattr(approval_mod, "prompt_approval", _prompt)

    async def resolver(approval_id: str, approved: bool, *, allow_always: bool = False) -> None:
        calls.append((approval_id, approved, allow_always))

    await approval_mod.maybe_handle_approval(
        {
            "status": "approval_pending",
            "approval_id": "pid-1",
            "command": "rm secret",
            "message": "Waiting for approval.",
        },
        live,
        resolver,
    )

    assert calls == [("pid-1", True, False)]
    assert live.stopped is True
    assert live.started is True
    assert "Approval pending" in buffer.getvalue()


@pytest.mark.asyncio
async def test_maybe_handle_approval_required_invokes_prompt_and_resolver(monkeypatch) -> None:
    live = _FakeLive()
    calls: list[tuple[str, bool, bool]] = []
    buffer = StringIO()
    monkeypatch.setattr(
        approval_mod,
        "console",
        Console(file=buffer, force_terminal=False, width=100, highlight=False),
    )

    async def _prompt(_: str, **_kwargs) -> str:
        return "o"

    monkeypatch.setattr(approval_mod, "prompt_approval", _prompt)

    async def resolver(approval_id: str, approved: bool, *, allow_always: bool = False) -> None:
        calls.append((approval_id, approved, allow_always))

    await approval_mod.maybe_handle_approval(
        {
            "status": "approval_required",
            "approval_id": "pid-2",
            "command": "rm secret",
            "warning": "Destructive command",
        },
        live,
        resolver,
    )

    assert calls == [("pid-2", True, False)]
    assert "Approval required" in buffer.getvalue()


@pytest.mark.asyncio
async def test_maybe_handle_approval_renders_full_command_without_truncation(monkeypatch) -> None:
    live = _FakeLive()
    buffer = StringIO()
    monkeypatch.setattr(
        approval_mod,
        "console",
        Console(file=buffer, force_terminal=False, width=120, highlight=False),
    )

    async def _prompt(_: str, **_kwargs) -> str:
        return "o"

    monkeypatch.setattr(approval_mod, "prompt_approval", _prompt)

    calls: list[tuple[str, bool, bool]] = []

    async def resolver(approval_id: str, approved: bool, *, allow_always: bool = False) -> None:
        calls.append((approval_id, approved, allow_always))

    long_script = (
        '"""Long setup script preamble that exceeds two hundred characters in total length.\n'
        "Importing required modules and configuring parameters before doing any work.\n"
        '"""\n'
        "import os\n"
        "import shutil\n"
        'shutil.rmtree("/tmp/target_directory")\n'
    )
    assert len(long_script) > 200

    await approval_mod.maybe_handle_approval(
        {
            "status": "approval_required",
            "approval_id": "pid-long-code",
            "command": long_script,
            "warning": "Destructive Python operation",
        },
        live,
        resolver,
    )

    assert calls == [("pid-long-code", True, False)]
    rendered = buffer.getvalue()
    assert "Approval required" in rendered
    # Confirm downstream CLI renderer shows the entire command including the destructive tail
    # past character 200.
    assert "Long setup script preamble" in rendered
    assert 'shutil.rmtree("/tmp/target_directory")' in rendered
