"""
Fixed-fractional position sizing: risk 1% of equity per trade.
units = (equity * risk_pct) / (stop_atr_mult * ATR)
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN


def compute_position_size(
    equity: float,
    atr_value: float,
    risk_pct: float = 0.01,
    price: float = 0.0,
    lot_size: float = 0.00001,
    min_notional: float = 5.0,
    stop_atr_mult: float = 2.0,
) -> float:
    """
    Calculate position size in base currency units.
    Returns 0 if the calculated size is below exchange minimums.
    """
    if atr_value <= 0 or equity <= 0 or price <= 0:
        return 0.0

    stop_distance = stop_atr_mult * atr_value
    risk_eur = equity * risk_pct
    units = risk_eur / stop_distance

    # Round down to lot size
    if lot_size > 0:
        d_units = Decimal(str(units))
        d_lot = Decimal(str(lot_size))
        units = float(d_units.quantize(d_lot, rounding=ROUND_DOWN))

    # Check minimum notional
    notional = units * price
    if notional < min_notional:
        return 0.0

    return units


def compute_stop_distance(atr_value: float, multiplier: float = 2.0) -> float:
    """Stop distance in price units."""
    return atr_value * multiplier
