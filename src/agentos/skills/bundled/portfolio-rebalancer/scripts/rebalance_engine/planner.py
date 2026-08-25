"""Rebalancing planner and simulation logic."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from .math import compute_asset_drift, quantize_amount, quantize_usd
from .models import (
    AssetDrift,
    AssetPosition,
    PortfolioState,
    RebalanceOrder,
    RebalancePlan,
    SimulationResult,
    TargetAllocation,
)


def calculate_drift(
    portfolio: PortfolioState,
    targets: Sequence[TargetAllocation],
    *,
    drift_tolerance_pct: Decimal = Decimal("5.0"),
    cash_buffer_pct: Decimal = Decimal("0.0"),
) -> list[AssetDrift]:
    """Public helper to calculate drift metrics for a portfolio."""
    return compute_asset_drift(
        portfolio,
        targets,
        drift_tolerance_pct=drift_tolerance_pct,
        cash_buffer_pct=cash_buffer_pct,
    )


def generate_rebalance_plan(
    portfolio: PortfolioState,
    targets: Sequence[TargetAllocation],
    prices: Mapping[str, Decimal] | None = None,
    *,
    drift_tolerance_pct: Decimal = Decimal("5.0"),
    min_trade_usd: Decimal = Decimal("10.0"),
    fee_rate_bps: Decimal = Decimal("10.0"),  # 10 bps = 0.10% trade fee
    cash_buffer_pct: Decimal = Decimal("0.0"),
) -> RebalancePlan:
    """Generate an optimal execution plan with minimal rebalancing trades.

    Sorts orders with SELLs first to generate cash liquidity before executing BUYs.
    """
    total_val = portfolio.total_portfolio_value_usd
    cash_buffer_usd = total_val * (cash_buffer_pct / Decimal("100"))

    # Resolve price map: explicit prices > position prices
    price_map: dict[str, Decimal] = {}
    if prices:
        for k, v in prices.items():
            price_map[k.upper()] = v
    for p in portfolio.positions:
        if p.symbol.upper() not in price_map and p.price_usd > Decimal("0"):
            price_map[p.symbol.upper()] = p.price_usd

    drifts = compute_asset_drift(
        portfolio,
        targets,
        drift_tolerance_pct=drift_tolerance_pct,
        cash_buffer_pct=cash_buffer_pct,
    )

    any_exceeds = any(d.exceeds_tolerance for d in drifts)
    if not any_exceeds or total_val <= Decimal("0"):
        return RebalancePlan(
            portfolio_value_usd=quantize_usd(total_val),
            cash_buffer_usd=quantize_usd(cash_buffer_usd),
            drifts=tuple(drifts),
            orders=(),
            total_trade_volume_usd=Decimal("0"),
            total_estimated_fees_usd=Decimal("0"),
            rebalance_needed=False,
            summary="All assets are within the specified drift tolerance. No rebalancing required.",
        )

    fee_multiplier = fee_rate_bps / Decimal("10000")
    sells: list[RebalanceOrder] = []
    buys: list[RebalanceOrder] = []

    for drift in drifts:
        symbol = drift.symbol
        diff = drift.diff_usd
        abs_diff = abs(diff)

        # Skip if trade size is below minimum threshold
        if abs_diff < min_trade_usd:
            continue

        price = price_map.get(symbol.upper(), Decimal("0"))
        if price <= Decimal("0"):
            continue

        trade_amount = quantize_amount(abs_diff / price)
        if trade_amount <= Decimal("0"):
            continue

        trade_value = quantize_usd(abs_diff)
        est_fee = quantize_usd(trade_value * fee_multiplier)

        if diff < Decimal("0"):
            # SELL order to trim overweight position
            sells.append(
                RebalanceOrder(
                    symbol=symbol,
                    side="SELL",
                    amount=trade_amount,
                    price_usd=quantize_usd(price),
                    value_usd=trade_value,
                    estimated_fee_usd=est_fee,
                    reason=(f"Trim overweight position ({drift.relative_drift_pct:.1f}% drift)"),
                )
            )
        elif diff > Decimal("0"):
            # BUY order to top up underweight position
            buys.append(
                RebalanceOrder(
                    symbol=symbol,
                    side="BUY",
                    amount=trade_amount,
                    price_usd=quantize_usd(price),
                    value_usd=trade_value,
                    estimated_fee_usd=est_fee,
                    reason=(f"Top up underweight position ({drift.relative_drift_pct:.1f}% drift)"),
                )
            )

    # Order execution sequence: SELLs first, then BUYs
    all_orders = tuple(sells + buys)
    total_volume = sum((o.value_usd for o in all_orders), Decimal("0"))
    total_fees = sum((o.estimated_fee_usd for o in all_orders), Decimal("0"))

    summary_text = (
        f"Rebalance needed: {len(all_orders)} trade(s) generated "
        f"({len(sells)} sell(s), {len(buys)} buy(s)) with total volume ${total_volume:,.2f} "
        f"and estimated fees ${total_fees:,.2f}."
    )

    return RebalancePlan(
        portfolio_value_usd=quantize_usd(total_val),
        cash_buffer_usd=quantize_usd(cash_buffer_usd),
        drifts=tuple(drifts),
        orders=all_orders,
        total_trade_volume_usd=quantize_usd(total_volume),
        total_estimated_fees_usd=quantize_usd(total_fees),
        rebalance_needed=len(all_orders) > 0,
        summary=summary_text,
    )


def simulate_rebalance(
    portfolio: PortfolioState,
    targets: Sequence[TargetAllocation],
    prices: Mapping[str, Decimal] | None = None,
    *,
    drift_tolerance_pct: Decimal = Decimal("5.0"),
    min_trade_usd: Decimal = Decimal("10.0"),
    fee_rate_bps: Decimal = Decimal("10.0"),
    cash_buffer_pct: Decimal = Decimal("0.0"),
) -> SimulationResult:
    """Simulate execution of generated orders and return before/after comparison."""
    plan = generate_rebalance_plan(
        portfolio,
        targets,
        prices,
        drift_tolerance_pct=drift_tolerance_pct,
        min_trade_usd=min_trade_usd,
        fee_rate_bps=fee_rate_bps,
        cash_buffer_pct=cash_buffer_pct,
    )

    # Compute max drift before
    initial_drifts = plan.drifts
    max_drift_before = max(
        (abs(d.absolute_drift) for d in initial_drifts), default=Decimal("0")
    ) * Decimal("100")

    if not plan.orders:
        return SimulationResult(
            initial_state=portfolio,
            projected_state=portfolio,
            plan=plan,
            max_drift_before=quantize_usd(max_drift_before),
            max_drift_after=quantize_usd(max_drift_before),
            turnover_pct=Decimal("0.0"),
        )

    # Project new positions
    new_positions_map: dict[str, AssetPosition] = {}
    for p in portfolio.positions:
        new_positions_map[p.symbol.upper()] = p

    new_cash = portfolio.cash_usd
    total_volume = Decimal("0")

    # Apply SELLs
    for order in plan.orders:
        if order.side == "SELL":
            curr_pos = new_positions_map.get(order.symbol.upper())
            curr_amt = curr_pos.amount if curr_pos else Decimal("0")
            new_amt = max(Decimal("0"), curr_amt - order.amount)
            new_positions_map[order.symbol.upper()] = AssetPosition(
                symbol=order.symbol,
                amount=new_amt,
                price_usd=order.price_usd,
            )
            new_cash += order.value_usd - order.estimated_fee_usd
            total_volume += order.value_usd

    # Apply BUYs
    for order in plan.orders:
        if order.side == "BUY":
            curr_pos = new_positions_map.get(order.symbol.upper())
            curr_amt = curr_pos.amount if curr_pos else Decimal("0")
            new_amt = curr_amt + order.amount
            new_positions_map[order.symbol.upper()] = AssetPosition(
                symbol=order.symbol,
                amount=new_amt,
                price_usd=order.price_usd,
            )
            new_cash -= order.value_usd + order.estimated_fee_usd
            total_volume += order.value_usd

    projected_state = PortfolioState(
        positions=tuple(new_positions_map.values()),
        cash_usd=quantize_usd(max(Decimal("0"), new_cash)),
    )

    # Compute max drift after
    post_drifts = compute_asset_drift(
        projected_state,
        targets,
        drift_tolerance_pct=drift_tolerance_pct,
        cash_buffer_pct=cash_buffer_pct,
    )
    max_drift_after = max(
        (abs(d.absolute_drift) for d in post_drifts), default=Decimal("0")
    ) * Decimal("100")

    init_val = portfolio.total_portfolio_value_usd
    turnover = (
        (total_volume / (Decimal("2") * init_val)) * Decimal("100")
        if init_val > Decimal("0")
        else Decimal("0")
    )

    return SimulationResult(
        initial_state=portfolio,
        projected_state=projected_state,
        plan=plan,
        max_drift_before=quantize_usd(max_drift_before),
        max_drift_after=quantize_usd(max_drift_after),
        turnover_pct=quantize_usd(turnover),
    )
