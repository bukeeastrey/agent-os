"""Data models for portfolio rebalancing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class AssetPosition:
    """Current holding for a single asset."""

    symbol: str
    amount: Decimal
    price_usd: Decimal

    @property
    def value_usd(self) -> Decimal:
        return self.amount * self.price_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "amount": str(self.amount),
            "price_usd": str(self.price_usd),
            "value_usd": str(self.value_usd),
        }


@dataclass(frozen=True)
class TargetAllocation:
    """Desired target weight for an asset."""

    symbol: str
    target_weight: Decimal  # e.g., Decimal("0.40") for 40%

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "target_weight": str(self.target_weight),
            "target_percentage": f"{self.target_weight * Decimal('100'):.2f}%",
        }


@dataclass(frozen=True)
class AssetDrift:
    """Drift metrics for a single asset position."""

    symbol: str
    current_value_usd: Decimal
    current_weight: Decimal
    target_weight: Decimal
    absolute_drift: Decimal  # current_weight - target_weight
    relative_drift_pct: Decimal  # (current_weight - target_weight) / target_weight * 100
    target_value_usd: Decimal
    diff_usd: Decimal  # target_value_usd - current_value_usd (positive = buy, negative = sell)
    exceeds_tolerance: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_value_usd": str(self.current_value_usd),
            "current_weight": str(self.current_weight),
            "current_percentage": f"{self.current_weight * Decimal('100'):.2f}%",
            "target_weight": str(self.target_weight),
            "target_percentage": f"{self.target_weight * Decimal('100'):.2f}%",
            "absolute_drift": str(self.absolute_drift),
            "relative_drift_pct": f"{self.relative_drift_pct:.2f}%",
            "target_value_usd": str(self.target_value_usd),
            "diff_usd": str(self.diff_usd),
            "exceeds_tolerance": self.exceeds_tolerance,
        }


@dataclass(frozen=True)
class PortfolioState:
    """Full snapshot of current positions."""

    positions: tuple[AssetPosition, ...]
    cash_usd: Decimal = Decimal("0")

    @property
    def total_asset_value_usd(self) -> Decimal:
        return sum((p.value_usd for p in self.positions), Decimal("0"))

    @property
    def total_portfolio_value_usd(self) -> Decimal:
        return self.total_asset_value_usd + self.cash_usd

    def get_position(self, symbol: str) -> AssetPosition | None:
        for p in self.positions:
            if p.symbol.upper() == symbol.upper():
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_asset_value_usd": str(self.total_asset_value_usd),
            "cash_usd": str(self.cash_usd),
            "total_portfolio_value_usd": str(self.total_portfolio_value_usd),
            "positions": [p.to_dict() for p in self.positions],
        }


@dataclass(frozen=True)
class RebalanceOrder:
    """Actionable buy or sell order to restore target weights."""

    symbol: str
    side: str  # "BUY" or "SELL"
    amount: Decimal
    price_usd: Decimal
    value_usd: Decimal
    estimated_fee_usd: Decimal = Decimal("0")
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "amount": str(self.amount),
            "price_usd": str(self.price_usd),
            "value_usd": str(self.value_usd),
            "estimated_fee_usd": str(self.estimated_fee_usd),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RebalancePlan:
    """Complete generated rebalance plan."""

    portfolio_value_usd: Decimal
    cash_buffer_usd: Decimal
    drifts: tuple[AssetDrift, ...]
    orders: tuple[RebalanceOrder, ...]
    total_trade_volume_usd: Decimal
    total_estimated_fees_usd: Decimal
    rebalance_needed: bool
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_value_usd": str(self.portfolio_value_usd),
            "cash_buffer_usd": str(self.cash_buffer_usd),
            "rebalance_needed": self.rebalance_needed,
            "total_trade_volume_usd": str(self.total_trade_volume_usd),
            "total_estimated_fees_usd": str(self.total_estimated_fees_usd),
            "summary": self.summary,
            "drifts": [d.to_dict() for d in self.drifts],
            "orders": [o.to_dict() for o in self.orders],
        }


@dataclass(frozen=True)
class SimulationResult:
    """Result of dry-run execution of a rebalance plan."""

    initial_state: PortfolioState
    projected_state: PortfolioState
    plan: RebalancePlan
    max_drift_before: Decimal
    max_drift_after: Decimal
    turnover_pct: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_drift_before": str(self.max_drift_before),
            "max_drift_after": str(self.max_drift_after),
            "turnover_pct": f"{self.turnover_pct:.2f}%",
            "plan": self.plan.to_dict(),
            "initial_state": self.initial_state.to_dict(),
            "projected_state": self.projected_state.to_dict(),
        }
