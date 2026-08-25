"""Mathematical calculations and helpers for portfolio rebalancing."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from .models import AssetDrift, PortfolioState, TargetAllocation

# Precision rounding helper
DECIMAL_PLACES_USD = Decimal("0.01")
DECIMAL_PLACES_AMOUNT = Decimal("0.00000001")


def quantize_usd(val: Decimal) -> Decimal:
    return val.quantize(DECIMAL_PLACES_USD, rounding=ROUND_HALF_UP)


def quantize_amount(val: Decimal) -> Decimal:
    return val.quantize(DECIMAL_PLACES_AMOUNT, rounding=ROUND_HALF_UP)


def normalize_targets(targets: Sequence[TargetAllocation]) -> list[TargetAllocation]:
    """Ensure target weights sum to 1.0 (100%).

    Raises ValueError if weights are negative or total sum is zero.
    """
    if not targets:
        raise ValueError("Target allocations list cannot be empty.")

    total_weight = sum((t.target_weight for t in targets), Decimal("0"))
    if total_weight <= Decimal("0"):
        raise ValueError("Sum of target weights must be strictly positive.")

    # Check for negative weights
    for t in targets:
        if t.target_weight < Decimal("0"):
            raise ValueError(
                f"Target weight for {t.symbol} cannot be negative ({t.target_weight})."
            )

    # If within floating tolerance of 1.0, normalize cleanly
    if total_weight == Decimal("1.0"):
        return list(targets)

    return [
        TargetAllocation(
            symbol=t.symbol.upper(),
            target_weight=t.target_weight / total_weight,
        )
        for t in targets
    ]


def compute_asset_drift(
    portfolio: PortfolioState,
    targets: Sequence[TargetAllocation],
    *,
    drift_tolerance_pct: Decimal = Decimal("5.0"),  # 5% relative drift tolerance
    cash_buffer_pct: Decimal = Decimal("0.0"),  # optional cash buffer % to keep unallocated
) -> list[AssetDrift]:
    """Compute drift metrics for each asset against its target weight."""
    normalized_targets = {t.symbol.upper(): t.target_weight for t in normalize_targets(targets)}
    total_val = portfolio.total_portfolio_value_usd
    if total_val <= Decimal("0"):
        return []

    # Calculate investable portfolio value after cash buffer
    cash_buffer_usd = total_val * (cash_buffer_pct / Decimal("100"))
    rebalanceable_val = max(Decimal("0"), total_val - cash_buffer_usd)

    positions_by_symbol = {p.symbol.upper(): p for p in portfolio.positions}
    all_symbols = set(normalized_targets.keys()).union(positions_by_symbol.keys())

    drifts: list[AssetDrift] = []
    for symbol in sorted(all_symbols):
        pos = positions_by_symbol.get(symbol)
        curr_val = pos.value_usd if pos else Decimal("0")
        curr_weight = curr_val / total_val if total_val > Decimal("0") else Decimal("0")
        target_weight = normalized_targets.get(symbol, Decimal("0"))

        abs_drift = curr_weight - target_weight
        if target_weight > Decimal("0"):
            rel_drift_pct = (abs_drift / target_weight) * Decimal("100")
        else:
            rel_drift_pct = Decimal("100") if curr_weight > Decimal("0") else Decimal("0")

        target_val = rebalanceable_val * target_weight
        diff_usd = target_val - curr_val

        # Exceeds tolerance if absolute drift exceeds threshold or relative drift exceeds tolerance
        exceeds = (
            abs(rel_drift_pct) >= drift_tolerance_pct
            if target_weight > Decimal("0")
            else curr_weight > Decimal("0")
        )

        drifts.append(
            AssetDrift(
                symbol=symbol,
                current_value_usd=quantize_usd(curr_val),
                current_weight=curr_weight,
                target_weight=target_weight,
                absolute_drift=abs_drift,
                relative_drift_pct=rel_drift_pct,
                target_value_usd=quantize_usd(target_val),
                diff_usd=quantize_usd(diff_usd),
                exceeds_tolerance=exceeds,
            )
        )

    return drifts
