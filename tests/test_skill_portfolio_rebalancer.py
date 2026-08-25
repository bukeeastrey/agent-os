"""Tests for the portfolio-rebalancer bundled skill."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from agentos.skills.loader import SkillLoader
from agentos.skills.paths import default_bundled_skills_dir
from agentos.skills.types import SkillLayer

_SKILL_SCRIPTS_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "portfolio-rebalancer"
    / "scripts"
)
if str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))

from rebalance_engine.math import normalize_targets  # noqa: E402
from rebalance_engine.models import AssetPosition, PortfolioState, TargetAllocation  # noqa: E402
from rebalance_engine.planner import (  # noqa: E402
    calculate_drift,
    generate_rebalance_plan,
    simulate_rebalance,
)


def test_models_and_portfolio_state() -> None:
    positions = (
        AssetPosition(symbol="BTC", amount=Decimal("2.0"), price_usd=Decimal("50000.0")),
        AssetPosition(symbol="ETH", amount=Decimal("10.0"), price_usd=Decimal("3000.0")),
    )
    portfolio = PortfolioState(positions=positions, cash_usd=Decimal("5000.0"))

    assert portfolio.total_asset_value_usd == Decimal("130000.0")
    assert portfolio.total_portfolio_value_usd == Decimal("135000.0")

    btc_pos = portfolio.get_position("btc")
    assert btc_pos is not None
    assert btc_pos.value_usd == Decimal("100000.0")

    p_dict = portfolio.to_dict()
    assert p_dict["cash_usd"] == "5000.0"
    assert len(p_dict["positions"]) == 2


def test_normalize_targets_valid() -> None:
    raw_targets = [
        TargetAllocation(symbol="BTC", target_weight=Decimal("50")),
        TargetAllocation(symbol="ETH", target_weight=Decimal("30")),
        TargetAllocation(symbol="SOL", target_weight=Decimal("20")),
    ]
    normalized = normalize_targets(raw_targets)
    assert len(normalized) == 3
    assert sum((t.target_weight for t in normalized), Decimal("0")) == Decimal("1.0")
    assert normalized[0].target_weight == Decimal("0.50")
    assert normalized[1].target_weight == Decimal("0.30")
    assert normalized[2].target_weight == Decimal("0.20")


def test_normalize_targets_invalid() -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_targets([])

    with pytest.raises(ValueError, match="strictly positive"):
        normalize_targets([TargetAllocation(symbol="BTC", target_weight=Decimal("0"))])

    with pytest.raises(ValueError, match="negative"):
        normalize_targets(
            [
                TargetAllocation(symbol="BTC", target_weight=Decimal("0.8")),
                TargetAllocation(symbol="ETH", target_weight=Decimal("-0.1")),
            ]
        )


def test_compute_drift_and_tolerance() -> None:
    positions = (
        AssetPosition(
            symbol="BTC", amount=Decimal("1.0"), price_usd=Decimal("70000.0")
        ),  # $70k (70%)
        AssetPosition(
            symbol="ETH", amount=Decimal("10.0"), price_usd=Decimal("3000.0")
        ),  # $30k (30%)
    )
    portfolio = PortfolioState(positions=positions, cash_usd=Decimal("0"))
    targets = [
        TargetAllocation(symbol="BTC", target_weight=Decimal("0.50")),
        TargetAllocation(symbol="ETH", target_weight=Decimal("0.50")),
    ]

    drifts = calculate_drift(portfolio, targets, drift_tolerance_pct=Decimal("5.0"))
    assert len(drifts) == 2

    btc_drift = next(d for d in drifts if d.symbol == "BTC")
    assert btc_drift.current_weight == Decimal("0.70")
    assert btc_drift.target_weight == Decimal("0.50")
    assert btc_drift.absolute_drift == Decimal("0.20")
    assert btc_drift.relative_drift_pct == Decimal("40.0")  # (0.7-0.5)/0.5 = 40%
    assert btc_drift.diff_usd == Decimal("-20000.00")
    assert btc_drift.exceeds_tolerance is True

    eth_drift = next(d for d in drifts if d.symbol == "ETH")
    assert eth_drift.current_weight == Decimal("0.30")
    assert eth_drift.diff_usd == Decimal("20000.00")
    assert eth_drift.exceeds_tolerance is True


def test_generate_rebalance_plan_and_order_sequencing() -> None:
    positions = (
        AssetPosition(
            symbol="BTC", amount=Decimal("1.0"), price_usd=Decimal("60000.0")
        ),  # $60k (60%)
        AssetPosition(
            symbol="ETH", amount=Decimal("10.0"), price_usd=Decimal("2000.0")
        ),  # $20k (20%)
        AssetPosition(
            symbol="SOL", amount=Decimal("100.0"), price_usd=Decimal("200.0")
        ),  # $20k (20%)
    )
    portfolio = PortfolioState(positions=positions, cash_usd=Decimal("0"))
    # Target: 33.33% each
    targets = [
        TargetAllocation(symbol="BTC", target_weight=Decimal("0.33333333")),
        TargetAllocation(symbol="ETH", target_weight=Decimal("0.33333333")),
        TargetAllocation(symbol="SOL", target_weight=Decimal("0.33333334")),
    ]

    plan = generate_rebalance_plan(
        portfolio,
        targets,
        drift_tolerance_pct=Decimal("5.0"),
        min_trade_usd=Decimal("50.0"),
        fee_rate_bps=Decimal("10.0"),
    )

    assert plan.rebalance_needed is True
    assert len(plan.orders) == 3

    # Ensure SELLs are ordered before BUYs for liquidity
    sides = [o.side for o in plan.orders]
    assert sides[0] == "SELL"
    assert "BUY" in sides[1:]

    # Check total volume
    assert plan.total_trade_volume_usd > Decimal("0")
    assert plan.total_estimated_fees_usd > Decimal("0")


def test_simulate_rebalance_reduces_drift() -> None:
    positions = (
        AssetPosition(symbol="BTC", amount=Decimal("1.0"), price_usd=Decimal("60000.0")),
        AssetPosition(symbol="ETH", amount=Decimal("10.0"), price_usd=Decimal("3000.0")),
        AssetPosition(symbol="SOL", amount=Decimal("50.0"), price_usd=Decimal("200.0")),
    )
    portfolio = PortfolioState(positions=positions, cash_usd=Decimal("0"))
    targets = [
        TargetAllocation(symbol="BTC", target_weight=Decimal("0.40")),
        TargetAllocation(symbol="ETH", target_weight=Decimal("0.40")),
        TargetAllocation(symbol="SOL", target_weight=Decimal("0.20")),
    ]

    sim = simulate_rebalance(portfolio, targets, drift_tolerance_pct=Decimal("5.0"))
    assert sim.max_drift_after < sim.max_drift_before
    assert sim.max_drift_after <= Decimal("0.05")
    assert sim.turnover_pct > Decimal("0")


def test_cli_selftest(tmp_path: Path) -> None:
    script_path = (
        Path(__file__).parent.parent
        / "src"
        / "agentos"
        / "skills"
        / "bundled"
        / "portfolio-rebalancer"
        / "scripts"
        / "rebalancer.py"
    )
    assert script_path.exists()

    result = subprocess.run(
        [sys.executable, str(script_path), "selftest"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "PASSED successfully" in result.stdout


def test_cli_drift_and_plan(tmp_path: Path) -> None:
    script_path = (
        Path(__file__).parent.parent
        / "src"
        / "agentos"
        / "skills"
        / "bundled"
        / "portfolio-rebalancer"
        / "scripts"
        / "rebalancer.py"
    )

    portfolio_file = tmp_path / "portfolio.json"
    portfolio_file.write_text(
        json.dumps(
            {
                "cash_usd": "0",
                "positions": [
                    {"symbol": "BTC", "amount": "1.0", "price_usd": "60000.0"},
                    {"symbol": "ETH", "amount": "10.0", "price_usd": "3000.0"},
                ],
            }
        ),
        encoding="utf-8",
    )

    targets_file = tmp_path / "targets.json"
    targets_file.write_text(
        json.dumps(
            {
                "targets": [
                    {"symbol": "BTC", "target_weight": "0.50"},
                    {"symbol": "ETH", "target_weight": "0.50"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # CLI drift with JSON output
    res_drift = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "drift",
            "--portfolio",
            str(portfolio_file),
            "--targets",
            str(targets_file),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert res_drift.returncode == 0
    drift_data = json.loads(res_drift.stdout)
    assert "drifts" in drift_data
    assert len(drift_data["drifts"]) == 2

    # CLI plan with JSON output
    res_plan = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "plan",
            "--portfolio",
            str(portfolio_file),
            "--targets",
            str(targets_file),
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert res_plan.returncode == 0
    plan_data = json.loads(res_plan.stdout)
    assert plan_data["rebalance_needed"] is True
    assert len(plan_data["orders"]) == 2

    # CLI plan with external prices and dry-run
    prices_file = tmp_path / "prices.json"
    prices_file.write_text(
        json.dumps(
            {
                "prices": {
                    "BTC": "65000.0",
                    "ETH": "3200.0",
                }
            }
        ),
        encoding="utf-8",
    )

    res_plan_prices = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "plan",
            "--portfolio",
            str(portfolio_file),
            "--targets",
            str(targets_file),
            "--prices",
            str(prices_file),
            "--dry-run",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert res_plan_prices.returncode == 0
    plan_prices_data = json.loads(res_plan_prices.stdout)
    assert plan_prices_data["rebalance_needed"] is True
    assert len(plan_prices_data["orders"]) == 2


def test_bundled_skill_loader_discovery() -> None:
    loader = SkillLoader(bundled_dir=default_bundled_skills_dir())
    skills = loader.load_all()
    rebalancer_skill = next((s for s in skills if s.name == "portfolio-rebalancer"), None)

    assert rebalancer_skill is not None
    assert rebalancer_skill.layer == SkillLayer.BUNDLED
    assert rebalancer_skill.metadata.category == "crypto"
    assert rebalancer_skill.metadata.emoji == "⚖️"
