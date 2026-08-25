#!/usr/bin/env python3
"""Standalone CLI interface for the Portfolio Rebalancer skill."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure local script engine is on sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from rebalance_engine.models import AssetPosition, PortfolioState, TargetAllocation  # noqa: E402
from rebalance_engine.planner import (  # noqa: E402
    calculate_drift,
    generate_rebalance_plan,
    simulate_rebalance,
)


def _parse_portfolio_payload(data: dict[str, Any]) -> PortfolioState:
    cash = Decimal(str(data.get("cash_usd", "0")))
    raw_positions = data.get("positions", [])
    positions: list[AssetPosition] = []
    for item in raw_positions:
        positions.append(
            AssetPosition(
                symbol=str(item["symbol"]).upper(),
                amount=Decimal(str(item["amount"])),
                price_usd=Decimal(str(item["price_usd"])),
            )
        )
    return PortfolioState(positions=tuple(positions), cash_usd=cash)


def _parse_targets_payload(data: dict[str, Any] | list[Any]) -> list[TargetAllocation]:
    targets: list[TargetAllocation] = []
    if isinstance(data, list):
        for item in data:
            targets.append(
                TargetAllocation(
                    symbol=str(item["symbol"]).upper(),
                    target_weight=Decimal(str(item["target_weight"])),
                )
            )
    elif isinstance(data, dict):
        raw = data.get("targets", data)
        if isinstance(raw, list):
            for item in raw:
                targets.append(
                    TargetAllocation(
                        symbol=str(item["symbol"]).upper(),
                        target_weight=Decimal(str(item["target_weight"])),
                    )
                )
        elif isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, dict):
                    targets.append(
                        TargetAllocation(
                            symbol=str(v.get("symbol", k)).upper(),
                            target_weight=Decimal(str(v.get("target_weight", "0"))),
                        )
                    )
                else:
                    targets.append(
                        TargetAllocation(
                            symbol=str(k).upper(),
                            target_weight=Decimal(str(v)),
                        )
                    )
    return targets


def _parse_prices_payload(data: dict[str, Any] | list[Any]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    if isinstance(data, dict):
        raw = data.get("prices", data)
        if isinstance(raw, dict):
            for k, v in raw.items():
                prices[str(k).upper()] = Decimal(str(v))
        elif isinstance(raw, list):
            for item in raw:
                prices[str(item["symbol"]).upper()] = Decimal(
                    str(item.get("price_usd", item.get("price", "0")))
                )
    elif isinstance(data, list):
        for item in data:
            prices[str(item["symbol"]).upper()] = Decimal(
                str(item.get("price_usd", item.get("price", "0")))
            )
    return prices


def cmd_drift(args: argparse.Namespace) -> int:
    with open(args.portfolio, encoding="utf-8") as f:
        portfolio_data = json.load(f)
    with open(args.targets, encoding="utf-8") as f:
        targets_data = json.load(f)

    portfolio = _parse_portfolio_payload(portfolio_data)
    targets = _parse_targets_payload(targets_data)
    tolerance = Decimal(str(args.tolerance))

    drifts = calculate_drift(portfolio, targets, drift_tolerance_pct=tolerance)

    if args.json:
        print(json.dumps({"drifts": [d.to_dict() for d in drifts]}, indent=2))
        return 0

    print("\n=== Portfolio Allocation Drift Analysis ===")
    print(
        f"Total Portfolio Value: ${portfolio.total_portfolio_value_usd:,.2f} "
        f"(Cash: ${portfolio.cash_usd:,.2f})\n"
    )
    hdr = (
        f"{'Asset':<8} {'Current ($)':<14} {'Current (%)':<12} "
        f"{'Target (%)':<12} {'Drift (%)':<12} {'Action Value':<14} {'Rebalance?':<10}"
    )
    print(hdr)
    print("-" * len(hdr))
    for d in drifts:
        action_str = (
            f"+${d.diff_usd:,.2f}" if d.diff_usd >= Decimal("0") else f"-${abs(d.diff_usd):,.2f}"
        )
        rebal_str = "YES" if d.exceeds_tolerance else "NO"
        c_pct = d.current_weight * Decimal("100")
        t_pct = d.target_weight * Decimal("100")
        print(
            f"{d.symbol:<8} ${d.current_value_usd:<13,.2f} {c_pct:>8.2f}%    "
            f"{t_pct:>8.2f}%    {d.relative_drift_pct:>8.2f}%    "
            f"{action_str:<14} {rebal_str:<10}"
        )
    print("")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    with open(args.portfolio, encoding="utf-8") as f:
        portfolio_data = json.load(f)
    with open(args.targets, encoding="utf-8") as f:
        targets_data = json.load(f)

    prices: dict[str, Decimal] | None = None
    if getattr(args, "prices", None):
        with open(args.prices, encoding="utf-8") as f:
            prices = _parse_prices_payload(json.load(f))

    portfolio = _parse_portfolio_payload(portfolio_data)
    targets = _parse_targets_payload(targets_data)
    tolerance = Decimal(str(args.tolerance))
    min_trade = Decimal(str(args.min_trade))
    fee_bps = Decimal(str(args.fee_bps))
    cash_buffer = Decimal(str(args.cash_buffer))

    plan = generate_rebalance_plan(
        portfolio,
        targets,
        prices=prices,
        drift_tolerance_pct=tolerance,
        min_trade_usd=min_trade,
        fee_rate_bps=fee_bps,
        cash_buffer_pct=cash_buffer,
    )

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    print("\n=== Generated Rebalance Plan [SAFETY: READ-ONLY DRY-RUN] ===")
    print(f"Summary: {plan.summary}")
    print(f"Portfolio Value: ${plan.portfolio_value_usd:,.2f}")
    print(f"Total Trade Volume: ${plan.total_trade_volume_usd:,.2f}")
    print(f"Estimated Execution Fees: ${plan.total_estimated_fees_usd:,.2f}\n")

    if not plan.orders:
        print("No rebalancing trades required.")
        return 0

    ord_hdr = (
        f"{'Order #':<8} {'Side':<6} {'Asset':<8} {'Amount':<14} "
        f"{'Price ($)':<12} {'Value ($)':<14} {'Est Fee':<10} {'Reason'}"
    )
    print(ord_hdr)
    print("-" * 95)
    for idx, order in enumerate(plan.orders, start=1):
        print(
            f"{idx:<8} {order.side:<6} {order.symbol:<8} {order.amount:<14.4f} "
            f"${order.price_usd:<11,.2f} ${order.value_usd:<13,.2f} "
            f"${order.estimated_fee_usd:<9,.2f} {order.reason}"
        )
    print("")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    with open(args.portfolio, encoding="utf-8") as f:
        portfolio_data = json.load(f)
    with open(args.targets, encoding="utf-8") as f:
        targets_data = json.load(f)

    prices: dict[str, Decimal] | None = None
    if getattr(args, "prices", None):
        with open(args.prices, encoding="utf-8") as f:
            prices = _parse_prices_payload(json.load(f))

    portfolio = _parse_portfolio_payload(portfolio_data)
    targets = _parse_targets_payload(targets_data)
    tolerance = Decimal(str(args.tolerance))
    min_trade = Decimal(str(args.min_trade))
    fee_bps = Decimal(str(args.fee_bps))
    cash_buffer = Decimal(str(args.cash_buffer))

    result = simulate_rebalance(
        portfolio,
        targets,
        prices=prices,
        drift_tolerance_pct=tolerance,
        min_trade_usd=min_trade,
        fee_rate_bps=fee_bps,
        cash_buffer_pct=cash_buffer,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    print("\n=== Rebalance Dry-Run Simulation ===")
    print(f"Pre-Rebalance Max Absolute Drift:  {result.max_drift_before:.2f}%")
    print(f"Post-Rebalance Max Absolute Drift: {result.max_drift_after:.2f}%")
    print(f"Portfolio Turnover:               {result.turnover_pct:.2f}%")
    print(f"Total Trade Volume:                ${result.plan.total_trade_volume_usd:,.2f}")
    print(f"Estimated Total Fees:              ${result.plan.total_estimated_fees_usd:,.2f}")
    print(f"Orders to Execute:                 {len(result.plan.orders)}\n")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    print("Running portfolio-rebalancer self-test...")

    # Test 1: Balanced 3-asset portfolio
    positions = (
        AssetPosition(
            symbol="BTC", amount=Decimal("1.0"), price_usd=Decimal("60000.0")
        ),  # $60k (60%)
        AssetPosition(
            symbol="ETH", amount=Decimal("10.0"), price_usd=Decimal("3000.0")
        ),  # $30k (30%)
        AssetPosition(
            symbol="SOL", amount=Decimal("50.0"), price_usd=Decimal("200.0")
        ),  # $10k (10%)
    )
    portfolio = PortfolioState(positions=positions, cash_usd=Decimal("0"))
    assert portfolio.total_portfolio_value_usd == Decimal("100000.0"), "Portfolio total mismatch"

    # Target: 40% BTC, 40% ETH, 20% SOL
    targets = [
        TargetAllocation(symbol="BTC", target_weight=Decimal("0.40")),
        TargetAllocation(symbol="ETH", target_weight=Decimal("0.40")),
        TargetAllocation(symbol="SOL", target_weight=Decimal("0.20")),
    ]

    drifts = calculate_drift(portfolio, targets, drift_tolerance_pct=Decimal("5.0"))
    assert len(drifts) == 3, "Expected 3 drift items"

    btc_drift = next(d for d in drifts if d.symbol == "BTC")
    assert btc_drift.current_weight == Decimal("0.60"), "BTC current weight mismatch"
    assert btc_drift.diff_usd == Decimal("-20000.00"), "BTC diff value mismatch"
    assert btc_drift.exceeds_tolerance is True, "BTC should exceed tolerance"

    plan = generate_rebalance_plan(portfolio, targets, drift_tolerance_pct=Decimal("5.0"))
    assert plan.rebalance_needed is True, "Plan should be needed"
    assert len(plan.orders) == 3, "Expected 3 orders"

    # Verify SELLs appear before BUYs
    first_order = plan.orders[0]
    assert first_order.side == "SELL", "First order should be a SELL for liquidity"
    assert first_order.symbol == "BTC", "First order should be BTC sell"

    # Verify simulation reduces max drift
    sim = simulate_rebalance(portfolio, targets, drift_tolerance_pct=Decimal("5.0"))
    assert sim.max_drift_after < sim.max_drift_before, (
        "Post drift should be strictly less than pre drift"
    )
    assert sim.max_drift_after <= Decimal("0.05"), "Post drift should be within tolerance"

    print("All portfolio-rebalancer self-test checks PASSED successfully.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="portfolio-rebalancer",
        description="Automated multi-asset portfolio rebalancing engine for AgentOS.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: drift
    drift_parser = subparsers.add_parser(
        "drift", help="Calculate allocation drift against target weights."
    )
    drift_parser.add_argument(
        "--portfolio", "-p", required=True, help="Path to portfolio JSON file."
    )
    drift_parser.add_argument("--targets", "-t", required=True, help="Path to targets JSON file.")
    drift_parser.add_argument(
        "--tolerance", default="5.0", help="Relative drift tolerance percentage (default: 5.0)."
    )
    drift_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    drift_parser.set_defaults(func=cmd_drift)

    # Subcommand: plan
    plan_parser = subparsers.add_parser(
        "plan", help="Generate optimal rebalance buy/sell trade orders (read-only dry-run)."
    )
    plan_parser.add_argument(
        "--portfolio", "-p", required=True, help="Path to portfolio JSON file."
    )
    plan_parser.add_argument("--targets", "-t", required=True, help="Path to targets JSON file.")
    plan_parser.add_argument("--prices", help="Optional path to external market prices JSON file.")
    plan_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Simulate trade plan without executing orders (default: True).",
    )
    plan_parser.add_argument(
        "--tolerance", default="5.0", help="Relative drift tolerance percentage (default: 5.0)."
    )
    plan_parser.add_argument(
        "--min-trade", default="10.0", help="Minimum trade value in USD (default: 10.0)."
    )
    plan_parser.add_argument(
        "--fee-bps", default="10.0", help="Estimated transaction fee in bps (default: 10.0)."
    )
    plan_parser.add_argument(
        "--cash-buffer",
        default="0.0",
        help="Cash buffer percentage to keep unallocated (default: 0.0).",
    )
    plan_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    plan_parser.set_defaults(func=cmd_plan)

    # Subcommand: simulate
    sim_parser = subparsers.add_parser(
        "simulate", help="Dry-run simulate execution of rebalance orders."
    )
    sim_parser.add_argument("--portfolio", "-p", required=True, help="Path to portfolio JSON file.")
    sim_parser.add_argument("--targets", "-t", required=True, help="Path to targets JSON file.")
    sim_parser.add_argument("--prices", help="Optional path to external market prices JSON file.")
    sim_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Run pure read-only simulation (default: True).",
    )
    sim_parser.add_argument(
        "--tolerance", default="5.0", help="Relative drift tolerance percentage (default: 5.0)."
    )
    sim_parser.add_argument(
        "--min-trade", default="10.0", help="Minimum trade value in USD (default: 10.0)."
    )
    sim_parser.add_argument(
        "--fee-bps", default="10.0", help="Estimated transaction fee in bps (default: 10.0)."
    )
    sim_parser.add_argument(
        "--cash-buffer",
        default="0.0",
        help="Cash buffer percentage to keep unallocated (default: 0.0).",
    )
    sim_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    sim_parser.set_defaults(func=cmd_simulate)

    # Subcommand: selftest
    test_parser = subparsers.add_parser("selftest", help="Run built-in engine verification suite.")
    test_parser.set_defaults(func=cmd_selftest)

    parsed = parser.parse_args()
    return parsed.func(parsed)


if __name__ == "__main__":
    sys.exit(main())
