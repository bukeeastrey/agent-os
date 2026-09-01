"""Regression tests for the gmgn-wallet-score bundled script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORE_SCRIPT = (
    ROOT
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "gmgn-wallet-score"
    / "scripts"
    / "score.py"
)


def test_score_script_handles_missing_args_gracefully() -> None:
    """Invoking score.py without args prints usage and exits cleanly with code 2."""
    result = subprocess.run(
        [sys.executable, str(SCORE_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage: score.py" in result.stdout


def test_score_script_handles_help_flag() -> None:
    """Invoking score.py with --help prints usage and exits with code 2."""
    result = subprocess.run(
        [sys.executable, str(SCORE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage: score.py" in result.stdout


def test_score_script_handles_single_arg() -> None:
    """Invoking score.py with only one argument prints usage and exits with code 2."""
    result = subprocess.run(
        [sys.executable, str(SCORE_SCRIPT), "0x1234"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage: score.py" in result.stdout
