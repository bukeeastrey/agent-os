"""Tests for usage.savings and usage.savings.pdf RPC endpoints."""

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

from agentos.gateway.rpc.registry import RpcContext
from agentos.gateway.rpc_usage import _handle_usage_savings, _handle_usage_savings_pdf


def _make_ctx() -> RpcContext:
    return RpcContext(
        conn_id="test",
        session_manager=None,
        usage_tracker=None,
        config=SimpleNamespace(),
    )


def test_usage_savings_rpc_empty_log_dir(tmp_path: Path) -> None:
    ctx = _make_ctx()
    payload = asyncio.run(_handle_usage_savings({"logDir": str(tmp_path)}, ctx))

    assert payload["turnsTotal"] == 0
    assert payload["turnsRouted"] == 0
    assert payload["routingSavingsUsd"] == 0.0
    assert payload["byRoute"] == []
    assert payload["byDay"] == []


def test_usage_savings_rpc_with_decisions(tmp_path: Path) -> None:
    log_file = tmp_path / "decisions-20260901.jsonl"
    entry = {
        "turn_id": "turn_1",
        "session_key": "agent:main:webchat:1",
        "prompt_hash": "a" * 16,
        "system_prompt_hash": "b" * 16,
        "tool_list_hash": "c" * 16,
        "tool_choice": "auto",
        "tokens_input": 1000,
        "tokens_output": 150,
        "model": "anthropic/claude-3-5-sonnet",
        "provider": "anthropic",
        "latency_ms": 120,
        "ts": "2026-09-01T12:00:00Z",
        "savings": {
            "baseline_model": "anthropic/claude-3-opus",
            "routed_model": "anthropic/claude-3-5-sonnet",
            "routing_confidence": 0.95,
            "routing_savings_pct": 80.0,
            "routing_savings_usd_estimated_vs_baseline": 0.012,
            "cost_usd": 0.003,
        },
    }
    log_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    ctx = _make_ctx()
    payload = asyncio.run(_handle_usage_savings({"logDir": str(tmp_path)}, ctx))

    assert payload["turnsTotal"] == 1
    assert payload["turnsRouted"] == 1
    assert payload["turnsRerouted"] == 1
    assert payload["routingSavingsUsd"] == 0.012
    assert len(payload["byRoute"]) == 1
    assert payload["byRoute"][0]["requestedModel"] == "anthropic/claude-3-opus"
    assert payload["byRoute"][0]["routedModel"] == "anthropic/claude-3-5-sonnet"


def test_usage_savings_pdf_rpc(tmp_path: Path) -> None:
    ctx = _make_ctx()
    payload = asyncio.run(_handle_usage_savings_pdf({"logDir": str(tmp_path)}, ctx))

    assert "pdfBase64" in payload
    assert "filename" in payload
    assert payload["filename"].endswith(".pdf")
    pdf_bytes = base64.b64decode(payload["pdfBase64"])
    assert pdf_bytes.startswith(b"%PDF")
