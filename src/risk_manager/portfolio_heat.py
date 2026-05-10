"""Portfolio heat: total open risk across all positions."""
from __future__ import annotations


def check_portfolio_heat(
    open_positions: list[dict],
    proposed_risk: float,
    equity: float,
    max_heat_pct: float = 0.05,
) -> bool:
    """
    Returns True if adding proposed_risk keeps total heat under the cap.
    Heat = sum of (units * |current_price - stop_price|) for all positions.
    """
    if equity <= 0:
        return False

    current_heat = sum(
        abs(p.get("current_price", p["entry_price"]) - p["stop_price"]) * p["units"]
        for p in open_positions
    )

    total_heat = current_heat + proposed_risk
    return (total_heat / equity) <= max_heat_pct
